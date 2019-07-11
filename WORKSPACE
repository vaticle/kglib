
workspace(
    name = "kglib"
)

########################################################################################################################
# Load Bazel Rules
########################################################################################################################

load("@bazel_tools//tools/build_defs/repo:git.bzl", "git_repository")
load("@bazel_tools//tools/build_defs/repo:http.bzl", "http_file")

git_repository(
    name = "io_bazel_rules_python",
    # Grakn python rules
    remote = "https://github.com/graknlabs/rules_python.git",
    commit = "4443fa25feac79b0e4c7c63ca84f87a1d6032f49",
)

## Only needed for PIP support:
load("@io_bazel_rules_python//python:pip.bzl", "pip_repositories", "pip3_import")
pip_repositories()

########################################################################################################################
# Load Bazel Distribution
########################################################################################################################

git_repository(
    name="graknlabs_bazel_distribution",
    remote="https://github.com/graknlabs/bazel-distribution",
    commit="27c8bf9e5d9f9b11b2a70dc2697da196d69f799c"
)

pip3_import(
    name = "pypi_deployment_dependencies",
    requirements = "@graknlabs_bazel_distribution//pip:requirements.txt"
)

load("@pypi_deployment_dependencies//:requirements.bzl", pip_install_deployment_requirements = "pip_install")
pip_install_deployment_requirements()

########################################################################################################################
# Load KGLIB's PyPi requirements
########################################################################################################################

# Load PyPI dependencies for Python programs
pip3_import(
    name = "pypi_dependencies",
    requirements = "//:requirements.txt",
)

load("@pypi_dependencies//:requirements.bzl", pip_install_kglib_requirements = "pip_install")
pip_install_kglib_requirements()


########################################################################################################################
# Load the pre-loaded Animal Trade Grakn distribution
########################################################################################################################

http_file(
  name = "animaltrade_dist",
  urls = ["https://storage.googleapis.com/kglib/grakn-core-all-mac-animaltrade1.5.3.zip", # TODO How to update to the latest relase each time?
  ]
)