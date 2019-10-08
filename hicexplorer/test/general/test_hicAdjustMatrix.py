import warnings
warnings.simplefilter(action="ignore", category=RuntimeWarning)
warnings.simplefilter(action="ignore", category=PendingDeprecationWarning)
import pytest
import os
from tempfile import NamedTemporaryFile

from hicexplorer import hicAdjustMatrix


ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "test_data/")
matrix = ROOT + 'small_test_matrix_50kb_res.h5'
outfile = NamedTemporaryFile(suffix='.h5', prefix='test_matrix', delete=True)
bed_file = ROOT + 'regions.bed'
bed_file_xfail = ROOT + 'regions_xfail.bed'


@pytest.mark.parametrize("matrix", [matrix])  # required
@pytest.mark.parametrize("outFileName", [outfile])  # required
@pytest.mark.parametrize("chromosomes", ['chrX', 'chr3R'])  # optional
@pytest.mark.parametrize("action", ['keep', 'remove', 'mask'])  # optional
@pytest.mark.parametrize("regions", [bed_file, None])  # optional
def test_trivial_run(matrix, outFileName, chromosomes, action, regions):
    """
        Test checks if all commandline args work in general.
    """
    args = "--matrix {} --outFileName {} --chromosomes {} --action {}".format(
        matrix,
        outFileName.name,
        chromosomes,
        action,
    ).split()

    if regions:
        args = "--matrix {} --outFileName {} --regions {} --action {}".format(
            matrix,
            outFileName.name,
            regions,
            action,
        ).split()

    hicAdjustMatrix.main(args)


@pytest.mark.parametrize("matrix", [matrix])  # required
@pytest.mark.parametrize("outFileName", [outfile])  # required
@pytest.mark.parametrize("chromosomes", ['chr10'])  # optional
@pytest.mark.parametrize("action", ['keep', 'remove', 'mask'])  # optional
@pytest.mark.xfail
def test_trivial_run_xfail(matrix, outFileName, chromosomes, action):
    """
        Test checks if all commandline args work in general.
    """
    args = "--matrix {} --outFileName {} --chromosomes {} --action {}".format(
        matrix,
        outFileName.name,
        chromosomes,
        action,
    ).split()

    hicAdjustMatrix.main(args)


@pytest.mark.parametrize("matrix", [matrix])  # required
@pytest.mark.parametrize("outFileName", [outfile])  # required
@pytest.mark.parametrize("chromosomes", ['chr10', 'chr11'])  # optional
@pytest.mark.parametrize("action", ['keep', 'remove', 'mask'])  # optional
@pytest.mark.xfail
def test_trivial_run_xfail_multichromosomes(matrix, outFileName, chromosomes, action):
    """
        Test checks if all commandline args work in general.
    """
    args = "--matrix {} --outFileName {} --chromosomes {} --action {}".format(
        matrix,
        outFileName.name,
        chromosomes,
        action,
    ).split()

    hicAdjustMatrix.main(args)


@pytest.mark.parametrize("matrix", [matrix])  # required
@pytest.mark.parametrize("outFileName", [outfile])  # required
@pytest.mark.parametrize("action", ['keep', 'remove', 'mask'])  # optional
@pytest.mark.parametrize("regions", [bed_file_xfail])  # optional
def test_trivial_run_xfail_regions(matrix, outFileName, action, regions):
    """
        Test checks if all commandline args work in general.
    """

    if regions:
        args = "--matrix {} --outFileName {} --regions {} --action {}".format(
            matrix,
            outFileName.name,
            regions,
            action,
        ).split()

    hicAdjustMatrix.main(args)
