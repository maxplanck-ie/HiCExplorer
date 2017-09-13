import sys, argparse
from scipy.sparse.linalg import eigs
import logging

from hicexplorer import HiCMatrix as hm
from hicexplorer._version import __version__

import matplotlib.pyplot as plt
import numpy as np

logging.basicConfig()
log = logging.getLogger("hicPCA")
log.setLevel(logging.WARN)


def parse_arguments():

    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        conflict_handler='resolve',
        usage="%(prog)s --matrix hic_matrix -o pca1.bw pca2.bw ",
        description="""
Computes PCA eigenvectors for the HiC matrix.

    $ hicPCA --matrix hic_matrix -o pca1.bw pca2.bw

"""
    )


    parser.add_argument('--matrix', '-m',
                        help='HiCExplorer matrix.',
                        required=True)

    parser.add_argument('--output', '-o',
                        help='file names for the pca 1 and pca 2 output files.',
                        nargs=2,
                        required=True)

    parser.add_argument('--format', '-f',
                        help='output format. Either bigwig (default) or bedgraph.',
                        choices=['bedgraph', 'bigwig'],
                        default = 'bigwig',
                        required=False)
    parser.add_argument('--chromosomes',
                        help='List of chromosomes to be included in the '
                        'correlation.',
                        default=None,
                        nargs='+')
    parser.add_argument('--version', action='version',
                        version='%(prog)s {}'.format(__version__))

    return parser


def main(args=None):
    args = parse_arguments().parse_args(args)

    ma = hm.hiCMatrix(args.matrix)
    if args.chromosomes:
        ma.keepOnlyTheseChr(args.chromosomes)
    ma.maskBins(ma.nan_bins)
    ma.matrix = ma.matrix.asfptype()
    # eigenvectors and eigenvalues for the from the matrix
    vals, vecs = eigs(ma.matrix, k=2, which='LM', ncv=50)

    # save eigenvectors
    chrom, start, end, _ = zip(*ma.cut_intervals)
    values1 = []
    values2 = []
    print("len(vecs)", len(vecs))
    for idx, outfile in enumerate(args.output):
        assert(len(vecs[:, idx]) == len(chrom))
        with open(outfile, 'w') as fh:
            for i, value in enumerate(vecs[:, idx]):
                fh.write("{}\t{}\t{}\t{}\n".format(chrom[i], start[i], end[i], value.real))
                if idx == 0:
                    values1.append(value.real)
                elif idx == 1:
                    values2.append(value.real)
                    
    x = np.arange(0, len(values1), 1)
    plt.fill_between(x, 0, values1)
    # plt.savefig("pca/pca1_plot.png")

    x = np.arange(0, len(values2), 1)
    plt.fill_between(x, 0, values2)
    plt.savefig("pca/pca2_plot.png", dpi=300)