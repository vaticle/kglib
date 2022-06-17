#
#  Copyright (C) 2022 Vaticle
#
#  Licensed to the Apache Software Foundation (ASF) under one
#  or more contributor license agreements.  See the NOTICE file
#  distributed with this work for additional information
#  regarding copyright ownership.  The ASF licenses this file
#  to you under the Apache License, Version 2.0 (the
#  "License"); you may not use this file except in compliance
#  with the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing,
#  software distributed under the License is distributed on an
#  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#  KIND, either express or implied.  See the License for the
#  specific language governing permissions and limitations
#  under the License.
#

import torch
from torch_geometric.nn import GCNConv
import torch.nn.functional as F
import torch_geometric.transforms as T
import inspect
import subprocess as sp
from typedb.client import *

from kglib.kgcn_data_loader.dataset.typedb_networkx_dataset import TypeDBNetworkxDataSet
from kglib.kgcn_data_loader.transform.standard_kgcn_transform import StandardKGCNNetworkxTransform
from kglib.kgcn_data_loader.utils import load_typeql_schema_file, load_typeql_data_file
from kglib.utils.graph.iterate import multidigraph_data_iterator, multidigraph_node_data_iterator
from kglib.utils.graph.query.query_graph import QueryGraph
from kglib.utils.graph.thing.queries_to_networkx_graph import build_graph_from_queries
from kglib.utils.typedb.synthetic.examples.diagnosis.generate import generate_example_data
from kglib.utils.typedb.type.type import get_thing_types, get_role_types

DATABASE = "diagnosis"
ADDRESS = "localhost:1729"

# Existing elements in the graph are those that pre-exist in the graph, and should be predicted to continue to exist
PREEXISTS = 0

# Candidates are neither present in the input nor in the solution, they are negative samples
CANDIDATE = 1

# Elements to infer are the graph elements whose existence we want to predict to be true, they are positive samples
TO_INFER = 2

# Categorical Attribute types and the values of their categories
CATEGORICAL_ATTRIBUTES = {'name': ['Diabetes Type II', 'Multiple Sclerosis', 'Blurred vision', 'Fatigue', 'Cigarettes',
                                   'Alcohol']}
# Continuous Attribute types and their min and max values
CONTINUOUS_ATTRIBUTES = {'severity': (0, 1), 'age': (7, 80), 'units-per-week': (3, 29)}

TYPES_TO_IGNORE = []
ROLES_TO_IGNORE = []

RELATION_TYPE_TO_PREDICT = 'diagnosis'
RELATION_TYPE_TO_PREDICT_ROLES = ['patient', 'diagnosis']

# The learner should see candidate relations the same as the ground truth relations, so adjust these candidates to
# look like their ground truth counterparts
TYPES_AND_ROLES_TO_OBFUSCATE = {}


def binary_relations_to_edges(graph):
    pass


def get_types(session, types_to_ignore, roles_to_ignore):
    with session.transaction(TransactionType.READ) as tx:
        # The terminology changes from here onwards from thing -> node and role -> edge
        node_types = get_thing_types(tx)
        edge_types = get_role_types(tx)
        # Ignore any types or roles that exist in the TypeDB instance but which aren't being used for learning to
        # reduce the number of categories to embed
        [node_types.remove(el) for el in types_to_ignore]
        [edge_types.remove(el) for el in roles_to_ignore]
        print(f'Found node types: {node_types}')
        print(f'Found edge types: {edge_types}')
        return node_types, edge_types


def diagnosis_example(typedb_binary_directory,
                      num_graphs=100,
                      num_processing_steps_tr=3,
                      num_processing_steps_ge=3,
                      num_training_iterations=50,
                      database=DATABASE,
                      address=ADDRESS,
                      schema_file_path="/Users/jamesfletcher/programming/research/kglib/utils/typedb/synthetic/examples/diagnosis/schema.tql",
                      seed_data_file_path="/Users/jamesfletcher/programming/research/kglib/utils/typedb/synthetic/examples/diagnosis/seed_data.tql"):
    """
    Run the diagnosis example from start to finish, including traceably ingesting predictions back into TypeDB

    Args:
        typedb_binary_directory: Location of the typedb binary for the purpose of loading initial schema and data
        num_graphs: Number of graphs to use for training and testing combined
        num_processing_steps_tr: The number of message-passing steps for training
        num_processing_steps_ge: The number of message-passing steps for testing
        num_training_iterations: The number of training epochs
        database: The name of the database to retrieve example subgraphs from
        address: The address of the running TypeDB instance
        schema_file_path: Path to the diagnosis schema file
        seed_data_file_path: Path to the file containing seed data, that doesn't grow as synthetic data is added

    Returns:
        Final accuracies for training and for testing
    """

    # Delete the database each time
    sp.check_call([
        './typedb',
        'console',
        f'--command=database delete {database}',
    ], cwd=typedb_binary_directory)

    tr_ge_split = int(num_graphs*0.5)

    client = TypeDB.core_client(address)
    if client.databases().contains(database):
        raise ValueError(
            f"There is already a database present with the name {database}. The Diagnosis example expects a clean DB. "
            f"Please delete the {database} database, or use another database name")
    client.databases().create(database)

    load_typeql_schema_file(database, typedb_binary_directory, schema_file_path)
    load_typeql_data_file(database, typedb_binary_directory, seed_data_file_path)
    generate_example_data(client, num_graphs, database=database)

    session = client.session(database, SessionType.DATA)

    node_types, edge_types = get_types(session, TYPES_TO_IGNORE, ROLES_TO_IGNORE)

    binary_relations_to_edges(graph)  # TODO: Should be a transform. Do we do this before or after generating features?

    # This transform adds the features and labels to the graph
    transform = StandardKGCNNetworkxTransform(
        node_types,
        edge_types,
        target_name='solution',  # TODO: We're planning to do away with already having the graphs labelled at this point, so somehow we need to add negative samples and label accordingly
        obfuscate=None,
        categorical=None,
        continuous=None,
        duplicate_in_reverse=True,
        label_attribute="concept",
    )

    # Create a Dataset that will load graphs from TypeDB on-demand, based on an ID
    dataset = TypeDBNetworkxDataSet(
        list(range(num_graphs)),
        get_query_handles,
        DATABASE,
        ADDRESS,
        session,
        True,
        transform
    )

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')

    data = dataset[0].to(device)  # Get the first graph object.

    data = T.ToUndirected()(data)
    del data['movie', 'rev_rates', 'user'].edge_label  # Remove "reverse" label.

    class GCN(torch.nn.Module):
        def __init__(self, hidden_channels):
            super().__init__()
            torch.manual_seed(1234567)
            # self.conv1 = GCNConv(dataset.num_features, hidden_channels)
            # self.conv2 = GCNConv(hidden_channels, dataset.num_classes)
            self.conv1 = GCNConv(3, hidden_channels)
            self.conv2 = GCNConv(hidden_channels, 3)

        def forward(self, x, edge_index):
            x = self.conv1(x, edge_index)
            x = x.relu()
            x = F.dropout(x, p=0.5, training=self.training)
            x = self.conv2(x, edge_index)
            return x

    model = GCN(hidden_channels=16)
    print(model)

    model = GCN(hidden_channels=16)
    model.eval()

    # out = model(data.x, data.edge_index)
    # visualize(out, color=data.y)

    model = GCN(hidden_channels=16)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01, weight_decay=5e-4)
    criterion = torch.nn.CrossEntropyLoss()

    def train():
        model.train()
        optimizer.zero_grad()  # Clear gradients.
        out = model(data.x, data.edge_index)  # Perform a single forward pass.
        # loss = criterion(out[data.train_mask], data.y[data.train_mask])  # Compute the loss solely based on the training nodes.
        loss = criterion(out, data.y)  # Compute the loss solely based on the training nodes.
        loss.backward()  # Derive gradients.
        optimizer.step()  # Update parameters based on gradients.
        return loss

    def test():
        model.eval()
        out = model(data.x, data.edge_index)
        pred = out.argmax(dim=1)  # Use the class with highest probability.
        # test_correct = pred[data.test_mask] == data.y[data.test_mask]  # Check against ground-truth labels.
        test_correct = pred == data.y  # Check against ground-truth labels.
        # test_acc = int(test_correct.sum()) / int(data.test_mask.sum())  # Derive ratio of correct predictions.
        test_acc = int(test_correct.sum()) / int(data.sum())  # Derive ratio of correct predictions.
        return test_acc

    # for epoch in range(1, 101):
    #     loss = train()
    #     print(f'Epoch: {epoch:03d}, Loss: {loss:.4f}')

    test_acc = test()
    print(f'Test Accuracy: {test_acc:.4f}')

    with session.transaction(TransactionType.WRITE) as tx:
        write_predictions_to_typedb(ge_graphs, tx)

    session.close()
    client.close()

    return solveds_tr, solveds_ge


def create_concept_graphs(example_indices, typedb_session, infer = True):
    """
    Builds an in-memory graph for each example, with an example_id as an anchor for each example subgraph.
    Args:
        example_indices: The values used to anchor the subgraph queries within the entire knowledge graph
        typedb_session: TypeDB Session

    Returns:
        In-memory graphs of TypeDB subgraphs
    """

    graphs = []

    options = TypeDBOptions.core()
    options.infer = infer

    for example_id in example_indices:
        print(f'Creating graph for example {example_id}')
        graph_query_handles = get_query_handles(example_id)

        with typedb_session.transaction(TransactionType.READ, options) as tx:
            # Build a graph from the queries, samplers, and query graphs
            graph = build_graph_from_queries(graph_query_handles, tx)

        obfuscate_labels(graph, TYPES_AND_ROLES_TO_OBFUSCATE)

        graph.name = example_id
        graphs.append(graph)

    return graphs


def obfuscate_labels(graph, types_and_roles_to_obfuscate):
    # Remove label leakage - change type labels that indicate candidates into non-candidates
    for data in multidigraph_data_iterator(graph):
        for label_to_obfuscate, with_label in types_and_roles_to_obfuscate.items():
            if data['type'] == label_to_obfuscate:
                data.update(type=with_label)
                break


def get_query_handles(example_id):
    """
    Creates an iterable, each element containing a TypeQL query, a function to sample the answers, and a QueryGraph
    object which must be the TypeDB graph representation of the query. This tuple is termed a "query_handle"

    Args:
        example_id: A uniquely identifiable attribute value used to anchor the results of the queries to a specific
                    subgraph

    Returns:
        query handles
    """
    assert example_id == 0

    # === Hereditary Feature ===
    hereditary_query = inspect.cleandoc(f'''match
           $p isa person;
           $par isa person;
           $ps(child: $p, parent: $par) isa parentship;
           $diag(patient:$par, diagnosed-disease: $d) isa diagnosis;
           $d isa disease, has name $n;
          ''')

    vars = p, par, ps, d, diag, n = 'p', 'par', 'ps', 'd', 'diag', 'n'
    hereditary_query_graph = (QueryGraph()
                              .add_vars(vars, PREEXISTS)
                              .add_role_edge(ps, p, 'child', PREEXISTS)
                              .add_role_edge(ps, par, 'parent', PREEXISTS)
                              .add_role_edge(diag, par, 'patient', PREEXISTS)
                              .add_role_edge(diag, d, 'diagnosed-disease', PREEXISTS)
                              .add_has_edge(d, n, PREEXISTS))

    # === Consumption Feature ===
    consumption_query = inspect.cleandoc(f'''match
           $p isa person;
           $s isa substance, has name $n;
           $c(consumer: $p, consumed-substance: $s) isa consumption, 
           has units-per-week $u;''')

    vars = p, s, n, c, u = 'p', 's', 'n', 'c', 'u'
    consumption_query_graph = (QueryGraph()
                               .add_vars(vars, PREEXISTS)
                               .add_has_edge(s, n, PREEXISTS)
                               .add_role_edge(c, p, 'consumer', PREEXISTS)
                               .add_role_edge(c, s, 'consumed-substance', PREEXISTS)
                               .add_has_edge(c, u, PREEXISTS))

    # === Age Feature ===
    person_age_query = inspect.cleandoc(f'''match 
            $p isa person, has age $a; 
           ''')

    vars = p, a = 'p', 'a'
    person_age_query_graph = (QueryGraph()
                              .add_vars(vars, PREEXISTS)
                              .add_has_edge(p, a, PREEXISTS))

    # === Risk Factors Feature ===
    risk_factor_query = inspect.cleandoc(f'''match 
            $d isa disease; 
            $p isa person; 
            $r(person-at-risk: $p, risked-disease: $d) isa risk-factor; 
           ''')

    vars = p, d, r = 'p', 'd', 'r'
    risk_factor_query_graph = (QueryGraph()
                               .add_vars(vars, PREEXISTS)
                               .add_role_edge(r, p, 'person-at-risk', PREEXISTS)
                               .add_role_edge(r, d, 'risked-disease', PREEXISTS))

    # === Symptom ===
    vars = p, s, sn, d, dn, sp, sev, c = 'p', 's', 'sn', 'd', 'dn', 'sp', 'sev', 'c'

    symptom_query = inspect.cleandoc(f'''match
           $p isa person;
           $s isa symptom, has name $sn;
           $d isa disease, has name $dn;
           $sp(presented-symptom: $s, symptomatic-patient: $p) isa symptom-presentation, has severity $sev;
           $c(cause: $d, effect: $s) isa causality;
          ''')

    symptom_query_graph = (QueryGraph()
                           .add_vars(vars, PREEXISTS)
                           .add_has_edge(s, sn, PREEXISTS)
                           .add_has_edge(d, dn, PREEXISTS)
                           .add_role_edge(sp, s, 'presented-symptom', PREEXISTS)
                           .add_has_edge(sp, sev, PREEXISTS)
                           .add_role_edge(sp, p, 'symptomatic-patient', PREEXISTS)
                           .add_role_edge(c, s, 'effect', PREEXISTS)
                           .add_role_edge(c, d, 'cause', PREEXISTS))

    # === Diagnosis ===

    diag, d, p, dn = 'diag', 'd', 'p', 'dn'

    diagnosis_query = inspect.cleandoc(f'''match
           $p isa person;
           $d isa disease, has name $dn;
           $diag(patient: $p, diagnosed-disease: $d) isa diagnosis;
          ''')

    diagnosis_query_graph = (QueryGraph()
                             .add_vars([diag], TO_INFER)
                             .add_vars([d, p, dn], PREEXISTS)
                             .add_role_edge(diag, d, 'diagnosed-disease', TO_INFER)
                             .add_role_edge(diag, p, 'patient', TO_INFER))

    # === Candidate Diagnosis ===
    candidate_diagnosis_query = inspect.cleandoc(f'''match
           $p isa person;
           $d isa disease, has name $dn;
           $diag(candidate-patient: $p, candidate-diagnosed-disease: $d) isa candidate-diagnosis; 
          ''')

    candidate_diagnosis_query_graph = (QueryGraph()
                                       .add_vars([diag], CANDIDATE)
                                       .add_vars([d, p, dn], PREEXISTS)
                                       .add_role_edge(diag, d, 'candidate-diagnosed-disease', CANDIDATE)
                                       .add_role_edge(diag, p, 'candidate-patient', CANDIDATE))

    return [
        (symptom_query, lambda x: x, symptom_query_graph),
        (diagnosis_query, lambda x: x, diagnosis_query_graph),
        (candidate_diagnosis_query, lambda x: x, candidate_diagnosis_query_graph),
        (risk_factor_query, lambda x: x, risk_factor_query_graph),
        (person_age_query, lambda x: x, person_age_query_graph),
        (consumption_query, lambda x: x, consumption_query_graph),
        (hereditary_query, lambda x: x, hereditary_query_graph)
    ]


def write_predictions_to_typedb(graphs, tx):
    """
    Take predictions from the ML model, and insert representations of those predictions back into the graph.

    Args:
        graphs: graphs containing the concepts, with their class predictions and class probabilities
        tx: TypeDB write transaction to use

    Returns: None

    """
    for graph in graphs:
        for node, data in graph.nodes(data=True):
            if data['prediction'] == 2:
                concept = data['concept']
                concept_type = concept.type_label
                if concept_type == 'diagnosis' or concept_type == 'candidate-diagnosis':
                    neighbours = graph.neighbors(node)

                    for neighbour in neighbours:
                        concept = graph.nodes[neighbour]['concept']
                        if concept.type_label == 'person':
                            person = concept
                        else:
                            disease = concept

                    p = data['probabilities']
                    query = (f'match '
                             f'$p iid {person.iid};'
                             f'$d iid {disease.iid};'
                             f'$kgcn isa kgcn;'
                             f'insert '
                             f'$pd(patient: $p, diagnosed-disease: $d, diagnoser: $kgcn) isa diagnosis,'
                             f'has probability-exists {p[2]:.3f},'
                             f'has probability-non-exists {p[1]:.3f},'  
                             f'has probability-preexists {p[0]:.3f};')
                    tx.query().insert(query)
    tx.commit()
