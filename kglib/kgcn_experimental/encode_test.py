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

import unittest

import numpy as np
import tensorflow as tf
from mock import Mock

from kglib.kgcn_experimental.encode import encode_types_one_hot, graph_to_input_target, TypeEncoder, make_mlp_model
from kglib.kgcn_experimental.genealogy.in_memory.data import create_graph
from kglib.kgcn_experimental.test.utils import get_call_args


class TestGraphToInputTarget(unittest.TestCase):
    def test_number_of_nodes_in_outputs_is_correct(self):
        all_node_types = ['person', 'parentship', 'siblingship']
        all_edge_types = ['parent', 'child', 'sibling']
        graph = create_graph(1)
        encode_types_one_hot(graph, all_node_types, all_edge_types, attribute='one_hot_type', type_attribute='type')

        expected_n_nodes = graph.number_of_nodes()

        input_graph, target_graph = graph_to_input_target(graph)
        self.assertEqual(expected_n_nodes, input_graph.number_of_nodes())
        self.assertEqual(expected_n_nodes, target_graph.number_of_nodes())

    def test_number_of_edges_in_outputs_is_correct(self):
        all_node_types = ['person', 'parentship', 'siblingship']
        all_edge_types = ['parent', 'child', 'sibling']
        graph = create_graph(1)
        encode_types_one_hot(graph, all_node_types, all_edge_types, attribute='one_hot_type', type_attribute='type')

        expected_n_edges = graph.number_of_edges()

        input_graph, target_graph = graph_to_input_target(graph)
        self.assertEqual(expected_n_edges, input_graph.number_of_edges())
        self.assertEqual(expected_n_edges, target_graph.number_of_edges())


class TestTypeEncoder(unittest.TestCase):
    def setUp(self):
        tf.enable_eager_execution()

    def test_type_encoding_stages_are_as_expected(self):
        op_mock = Mock(return_value=np.array([0.121, 1.621, 1.437, -0.194, -0.216], dtype=np.float64))

        encode = TypeEncoder(5, 0, op=op_mock)
        encoding = encode(np.array([[1, 0.1234]], dtype=np.float64))

        op_mock_call_args = get_call_args(op_mock)
        expected_one_hot_encoding = np.array([[0, 1, 0, 0, 0]], dtype=np.float64)
        np.testing.assert_array_equal(op_mock_call_args[0][0].numpy(), expected_one_hot_encoding)

        expected_encoding = np.array([0.121, 1.621, 1.437, -0.194, -0.216], dtype=np.float64)
        np.testing.assert_array_equal(expected_encoding, encoding)

    def test_default_op_works_as_expected(self):
        encode = TypeEncoder(5, 0, make_mlp_model(latent_size=16, num_layers=2))
        encoding = encode(tf.constant(np.array([[1, 0.1234]]), dtype=np.float64))
        self.assertEqual((1, 16), encoding.numpy().shape)
        print(encoding)


class TestAttributeEncoder(unittest.TestCase):
    def test_attribute_encoding_stages_are_as_expected(self):

        def op_mock():
            return Mock(return_value=np.array([0.121, 1.621, 1.437, -0.194, -0.216], dtype=np.float64))

        def attr_mock():
            return Mock(return_value=np.array([0.22632198, 0.29790161, 0.44993045], dtype=np.float64))

        encode = AttributeEncoder(5, 0, op=op_mock, attr_op=attr_mock)
        encoding = encode(np.array([2, 0.1234], dtype=np.float64))

        op_mock_call_args = get_call_args(op_mock)
        expected_intermediate_encoding = np.array([0, 0, 1, 0, 0, 0.22632198, 0.29790161, 0.44993045], dtype=np.float64)
        np.testing.assert_array_equal(op_mock_call_args, [[expected_intermediate_encoding]])

        attr_mock_call_args = get_call_args(attr_mock)
        expected_attribute_value = np.array([0.1234], dtype=np.float64)
        np.testing.assert_array_equal(attr_mock_call_args, [[expected_attribute_value]])

        expected_encoding = np.array([0.121, 1.621, 1.437, -0.194, -0.216], dtype=np.float64)
        np.testing.assert_array_equal(expected_encoding, encoding)


if __name__ == "__main__":
    unittest.main()
