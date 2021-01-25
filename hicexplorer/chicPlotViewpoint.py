import argparse
import sys
import os
import errno
import math
from multiprocessing import Process, Queue
import time
import traceback

import logging
log = logging.getLogger(__name__)

import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvas

import hicmatrix.HiCMatrix as hm
from hicexplorer import utilities
from hicexplorer._version import __version__
from .lib import Viewpoint

import h5py
import io
import tarfile
from contextlib import closing

def parse_arguments(args=None):
    parser = argparse.ArgumentParser(add_help=False,
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description="""
chicPlotViewpoint plots one or many viewpoints with the average background model and the computed p-value per sample. In addition, it can highlight differential interactions of two samples and/or significant regions.

An example usage is:

`$ chicPlotViewpoint --interactionFile viewpoint1.txt viewpoint2.txt --range 500000 500000  --backgroundModelFile background_model.txt --pValue --outFileName viewpoint1_2.png --dpi 300`


In batch mode the list of file names and the folders containing the files need to be given:

`$ chicPlotViewpoint --interactionFile viewpoint_names.txt -interactionFileFolder viewpointFilesFolder --differentialTestResult rejected_H0.txt --differentialTestResultsFolder differentialFolder --range 500000 500000 --backgroundModelFile background_model.txt --pValue --outputFolder plotsFOlder --dpi 300 --threads 20`

"""
                                     )
    parserRequired = parser.add_argument_group('Required arguments')

    parserRequired.add_argument('--interactionFile', '-if',
                                help='path to the interaction files which should be used for plotting',
                                required=True)

    parserRequired.add_argument('--range',
                                help='Defines the region upstream and downstream of a reference point which should be included. '
                                'Format is --region upstream downstream, e.g.: --region 500000 500000 plots 500kb up- and 500kb downstream. This value should not exceed the range used in the other chic-tools.',
                                required=True,
                                type=int,
                                nargs=2)

    parserOpt = parser.add_argument_group('Optional arguments')

    parserOpt.add_argument('--backgroundModelFile', '-bmf',
                           help='path to the background file which should be used for plotting',
                           required=False)
    # parserOpt.add_argument('--interactionFileFolder', '-iff',
    #                        help='Folder where the interaction files are stored. Applies only for batch mode'
    #                        ' (Default: %(default)s).',
    #                        required=False,
    #                        default='.')
    parserOpt.add_argument('--differentialTestResult', '-dif',
                           help='Path to the H0 rejected files to highlight the regions in the plot.',
                           required=False)
    # parserOpt.add_argument('--significantInteractionFileFolder', '-siff',
    #                        help='Folder where the files with detected significant interactions are stored. Applies only for batch mode'
    #                        ' (Default: %(default)s).',
    #                        required=False,
    #                        default='.')
    # parserOpt.add_argument('--differentialTestResultsFolder', '-diff',
    #                        help='Folder where the H0 rejected files are stored. Applies only for batch mode'
    #                        ' (Default: %(default)s).',
    #                        required=False,
    #                        default='.')
    parserOpt.add_argument('--significantInteractions', '-si',
                           help='Path to the files with detected significant interactions to highlight the regions in the plot.',
                           required=False)
    parserOpt.add_argument('--plotSignificantInteractions', '-psi',
                           help='Highlights the significant interactions in the plot itself. If not set, only the p-values are updated',
                           required=False,
                           action='store_true')
    parserOpt.add_argument('--outFileName', '-o',
                           help='Output tar.gz of the files'
                           ' (Default: %(default)s).',
                           required=False,
                           default='plots.tar.gz')
    parserOpt.add_argument('--outputFormat', '-format',
                           help='Output format of the plot'
                           ' (Default: %(default)s).',
                           required=False,
                           default='png')
    parserOpt.add_argument('--dpi',
                           help='Optional parameter: Resolution for the image, if'
                           'output is a raster graphics image (e.g png, jpg)'
                           ' (Default: %(default)s).',
                           type=int,
                           default=300,
                           required=False)
    # parserOpt.add_argument('--binResolution', '-r',
    #                        help='Resolution of the bin in genomic units. Values are set as number of bases, e.g. 1000 for a 1kb, 5000 for a 5kb or 10000 for a 10kb resolution'
    #                        ' (Default: %(default)s).',
    #                        type=int,
    #                        default=1000,
    #                        required=False)
    parserOpt.add_argument('--combinationMode',
                        '-cm',
                        help='This option defines how the interaction data should be computed and combined: '
                        'dual: Combines as follows: [[matrix1_gene1, matrix2_gene1], [matrix2_gene1, matrix3_gene1],[matrix1_gene2, matrix2_gene2], ...]'
                        'single: Combines as follows: [matrix1_gene1, matrix1_gene2, matrix2_gene1, ...], '
                        'allGenes: Combines as follows: [[matrix1_gene1, matrix2_gene1, matrix2_gene1], [matrix1_gene2, matrix2_gene2, matrix3_gene2], ...]'
                        'oneGene: Computes all data of one gene, please specify \'--\'. If a gene is not unique, each viewpoint is treated independently.'
                        'file: A user specific file (\'--\') with one entry per line and tab seperated: matrixName   geneName. Please define how many lines should be combined to one (\'--computeSampleNumber\').'
                        ' (Default: %(default)s).',
                        default='dual',
                        choices=['dual', 'single', 'allGenes', 'oneGene', 'file']
                        )

    parserOpt.add_argument('--combinationName', '-cn',
                           help='Gene name or file name for modes \'oneGene\' or \'file\' of parameter \'--combinationMode\''
                           ' (Default: %(default)s).',
                           required=False,
                           default=None)

    parserOpt.add_argument('--colorMapPvalue',
                           help='Color map to use for the p-value. Available '
                           'values can be seen here: '
                           'http://matplotlib.org/examples/color/colormaps_reference.html'
                           ' (Default: %(default)s).',
                           default='RdYlBu')
    parserOpt.add_argument('--maxPValue', '-map',
                           help='Maximal value for p-value. Values above this threshold are set to this value'
                           ' (Default: %(default)s).',
                           type=float,
                           default=0.1)
    parserOpt.add_argument('--minPValue', '-mp',
                           help='Minimal value for p-value. Values below this threshold are set to this value'
                           ' (Default: %(default)s).',
                           type=float,
                           default=0.0)

    parserOpt.add_argument('--pValue', '-p',
                           help='Plot p-values as a colorbar',
                           action='store_true'
                           )
    parserOpt.add_argument('--pValueSignificanceLevels', '-psl',
                           help='Highlight the p-values by the defined significance levels.',
                           type=float,
                           nargs='+'
                           )
    parserOpt.add_argument('--xFold', '-xf',
                           help='Plot x-fold region for the mean background.',
                           type=float,
                           default=None)
    parserOpt.add_argument('--truncateZeroPvalues', '-tzpv',
                           help='Sets all p-values which are equal to zero to one.',
                           required=False,
                           action='store_true')
    # parserOpt.add_argument('--outFileName', '-o',
    #                        help='File name to save the image. Not used in batch mode.')
    # parserOpt.add_argument('--batchMode', '-bm',
    #                        help='The given file for --interactionFile and or --targetFile contain a list of the to be processed files.',
    #                        required=False,
    #                        action='store_true')
    # parserOpt.add_argument('--plotSampleNumber', '-psn',
    #                        help='Number of samples per plot. Applies only in batch mode'
    #                        ' (Default: %(default)s).',
    #                        required=False,
    #                        default=2,
    #                        type=int)
    parserOpt.add_argument('--colorList', '-cl',
                           help='Colorlist for the viewpoint lines (Default g b c m y k).',
                           required=False,
                           default=['g', 'b', 'c', 'm', 'y', 'k'],
                           type=str,
                           nargs='+')
    parserOpt.add_argument('--threads', '-t',
                           help='Number of threads (uses the python multiprocessing module)'
                           ' (Default: %(default)s).',
                           required=False,
                           default=4,
                           type=int
                           )
    parserOpt.add_argument("--help", "-h", action="help", help="show this help message and exit")

    parserOpt.add_argument('--version', action='version',
                           version='%(prog)s {}'.format(__version__))
    return parser


def plot_images(pInteractionFileList, pHighlightDifferentialRegionsFileList, pBackgroundData, pArgs, pViewpointObj, pSignificantRegionsFileList, pResolution, pQueue=None):
    images_array = []
    file_name_list = []
    try:

        for j, interactionFile in enumerate(pInteractionFileList):
            number_of_rows_plot = len(interactionFile)
            matplotlib.rcParams.update({'font.size': 9})
            fig = plt.figure(figsize=(9.4, 4.8), dpi=pArgs.dpi)
            canvas = FigureCanvas(fig)
            z_score_heights = [0.07] * number_of_rows_plot
            viewpoint_height_ratio = 0.95 - (0.07 * number_of_rows_plot)
            if viewpoint_height_ratio < 0.4:
                viewpoint_height_ratio = 0.4
                _ratio = 0.6 / number_of_rows_plot
                z_score_heights = [_ratio] * number_of_rows_plot

            if pArgs.pValue:
                gs = gridspec.GridSpec(1 + len(interactionFile), 2, height_ratios=[0.95 - (0.07 * number_of_rows_plot), *z_score_heights], width_ratios=[0.75, 0.25])
                gs.update(hspace=0.5, wspace=0.05)
                ax1 = plt.subplot(gs[0, 0])
                ax1.margins(x=0)
            else:
                ax1 = plt.gca()
            colors = pArgs.colorList
            background_plot = True
            data_plot_label = None
            gene = ''
            file_name = []
            for i, interactionFile_ in enumerate(interactionFile):
                # if pArgs.interactionFileFolder != '.':
                #     absolute_path_interactionFile_ = pArgs.interactionFileFolder + '/' + interactionFile_
                # else:
                #     absolute_path_interactionFile_ = interactionFile_
                file_name.append(interactionFile_[0])
                # log.debug('interactionFile_ {}'.format(interactionFile_))
                data, background_data_plot, p_values, viewpoint_index_start, viewpoint_index_end, viewpoint = pViewpointObj.getDataForPlotting(pArgs.interactionFile, interactionFile_, pArgs.range, pBackgroundData, pResolution)
                # log.debug('data {}'.format(data))
                if len(data) <= 1 or len(p_values) <= 1:
                    log.warning('Only one data point in given range, no plot is created! Interaction file {} Range {}'.format(interactionFile_, pArgs.range))
                    continue
                # matrix_name, viewpoint, upstream_range, downstream_range, gene, _ = header.strip().split('\t')
                # log.debug('Matrix_name {}'.format(matrix_name))
                # matrix_name = os.path.basename(matrix_name)

                # matrix_name = matrix_name.split('.')[0]
                # log.debug('matrix_name {}'.format(matrix_name))
                # number_of_data_points = len(data)
                highlight_differential_regions = None
                significant_p_values = None
                significant_regions = None
                if pArgs.differentialTestResult:
                    # if pArgs.differentialTestResultsFolder != '.':
                    #     differentialFilePath = pArgs.differentialTestResultsFolder + '/' + pHighlightDifferentialRegionsFileList[j]
                    # else:
                    #     differentialFilePath = pHighlightDifferentialRegionsFileList[j]

                    highlight_differential_regions = pViewpointObj.readRejectedFile(pArgs.differentialTestResult, pHighlightDifferentialRegionsFileList[j], viewpoint_index_start, viewpoint_index_end, pResolution, pArgs.range, viewpoint)
                if pArgs.significantInteractions:
                    # if pArgs.significantInteractionFileFolder != '.':
                    #     significantInteractionsFilePath = pArgs.significantInteractionFileFolder + '/' + pSignificantRegionsFileList[j][i]
                    # else:
                    #     significantInteractionsFilePath = pSignificantRegionsFileList[j][i]
                    
                    significant_regions, significant_p_values = pViewpointObj.readSignificantRegionsFile(pArgs.significantInteractions, pSignificantRegionsFileList[j][i], viewpoint_index_start, viewpoint_index_end, pResolution, pArgs.range, viewpoint)
                    # significant_regions, significant_p_values = pViewpointObj.readSignificantRegionsFile(significantInteractionsFilePath, viewpoint_index_start, viewpoint_index_end, pResolution, pArgs.range, viewpoint)
                if not pArgs.plotSignificantInteractions:
                    significant_regions = None
                if data_plot_label:
                    data_plot_label += pViewpointObj.plotViewpoint(pAxis=ax1, pData=data, pColor=colors[i % len(colors)], pLabelName=':'.join(interactionFile_), pHighlightRegion=highlight_differential_regions, pHighlightSignificantRegion=significant_regions)
                else:
                    data_plot_label = pViewpointObj.plotViewpoint(pAxis=ax1, pData=data, pColor=colors[i % len(colors)], pLabelName=':'.join(interactionFile_), pHighlightRegion=highlight_differential_regions, pHighlightSignificantRegion=significant_regions)

                if background_plot:
                    # log.debug('background_data_plot {}'.format(len(background_data_plot)))
                    if background_data_plot is not None:
                        data_plot_label += pViewpointObj.plotBackgroundModel(pAxis=ax1, pBackgroundData=background_data_plot, pXFold=pArgs.xFold)
                    background_plot = False
                if pArgs.truncateZeroPvalues:
                    p_values = np.array(p_values, dtype=np.float32)
                    mask = p_values == 0.0
                    p_values[mask] = 1.0
                if pArgs.minPValue is not None or pArgs.maxPValue is not None:

                    p_values = np.array(p_values, dtype=np.float32)
                    if significant_p_values:
                        for location in significant_p_values:
                            for x in range(location[0], location[1]):
                                if x < len(p_values):
                                    p_values[x] = location[2]
                    p_values.clip(pArgs.minPValue, pArgs.maxPValue, p_values)

                if pArgs.pValue:
                    pViewpointObj.plotPValue(pAxis=plt.subplot(gs[1 + i, 0]), pAxisLabel=plt.subplot(gs[1 + i, 1]), pPValueData=p_values,
                                             pLabelText=':'.join(interactionFile_), pCmap=pArgs.colorMapPvalue,
                                             pFigure=fig, pValueSignificanceLevels=pArgs.pValueSignificanceLevels)

            if data_plot_label is not None:

                ticks = []
                x_labels = []

                if pArgs.range[0] + pArgs.range[1] <= 2e6:
                    divisor_legend = 1e3
                    mod_legend = 2e5

                    if pArgs.range[0] + pArgs.range[1] <= 1e4:
                        mod_legend = 5e3
                    elif pArgs.range[0] + pArgs.range[1] <= 5e4:
                        mod_legend = 1e4
                    elif pArgs.range[0] + pArgs.range[1] <= 1e5:
                        mod_legend = 5e4
                    elif pArgs.range[0] + pArgs.range[1] <= 5e5:
                        mod_legend = 1e5
                    # log.debug('divisor_legend {}'.format(divisor_legend))

                    unit = 'kb'
                elif pArgs.range[0] + pArgs.range[1] > 2e6:
                    divisor_legend = 1e6
                    mod_legend = 1e6
                    unit = 'Mb'

                for k, j in zip(range((pArgs.range[0])), range(pArgs.range[0], 1, -1)):
                    if j % mod_legend == 0:
                        x_labels.append(str(-int(j) // int(divisor_legend)) + unit)
                        ticks.append(k // pResolution)
                x_labels.append('RP')
                ticks.append(pArgs.range[0] // pResolution)

                referencepoint_index = ticks[-1]
                for k, j in zip(range(pArgs.range[1]), range(1, pArgs.range[1] + 1, 1)):
                    if j % mod_legend == 0:
                        x_labels.append(str(int(j) // int(divisor_legend)) + unit)
                        ticks.append(referencepoint_index + (k // pResolution))

                # log.debug('labels: {}'.format(x_labels))
                ax1.set_ylabel('Number of interactions')
                ax1.set_xticks(ticks)
                ax1.set_xticklabels(x_labels)

                # multiple legends in one figure
                data_legend = [label.get_label() for label in data_plot_label]
                ax1.legend(data_plot_label, data_legend, loc=0)

                #### TODO store other name, store as numpy array in hdf
                # sample_prefix = ""
                # if pArgs.outFileName:
                #     if pArgs.outputFolder != '.':
                #         outFileName = pArgs.outputFolder + '/' + pArgs.outFileName
                #     else:
                #         outFileName = pArgs.outFileName

                # else:
                #     for interactionFile_ in interactionFile:
                #         sample_prefix += interactionFile_.split('/')[-1].split('_')[0] + '_'
                #     if sample_prefix.endswith('_'):
                #         sample_prefix = sample_prefix[:-1]
                #     region_prefix = '_'.join(interactionFile[0].split('/')[-1].split('_')[1:4])
                #     outFileName = gene + '_' + sample_prefix + '_' + region_prefix
                #     if pArgs.outputFolder != '.':
                #         outFileName = pArgs.outputFolder + '/' + outFileName

                # if pArgs.outputFormat != outFileName.split('.')[-1]:
                #     outFileName = outFileName + '.' + pArgs.outputFormat  

                
                    # tar.add(source_dir, arcname=os.path.basename(source_dir))

                bufferObject = io.BytesIO()
                # plt.savefig(buf, format = 'png')
                plt.savefig(bufferObject, format=pArgs.outputFormat, dpi=300)
                images_array.append(bufferObject)
                # canvas.draw()
                # width, height = fig.get_size_inches() * fig.get_dpi()
                # image = canvas.buffer_rgba()
                # images_array.append(np.asarray(image))
                # log.debug('image {}'.format(image))
            plt.close(fig)
            file_name.append(interactionFile[0][2])
            file_name_list.append('_'.join(file_name))
    except Exception as exp:
        pQueue.put('Fail: ' + str(exp)+ traceback.format_exc())
        return
    if pQueue is None:
        return
    pQueue.put([images_array,file_name_list])
    return


def main(args=None):
    args = parse_arguments().parse_args(args)
    viewpointObj = Viewpoint()
    background_data = None

    # if not os.path.exists(args.outputFolder):
    #     try:
    #         os.makedirs(args.outputFolder)
    #     except OSError as exc:  # Guard against race condition
    #         if exc.errno != errno.EEXIST:
    #             raise
    if args.pValueSignificanceLevels:
        old = -100
        for element in args.pValueSignificanceLevels:
            if old < element:
                old = element
                continue
            else:
                log.error('--pValueSignificanceLevels levels need to increase: {}'.format(args.pValueSignificanceLevels))
                exit(1)
    if args.backgroundModelFile:
        background_data = viewpointObj.readBackgroundDataFile(args.backgroundModelFile, args.range, args.range[1], pMean=True)

    interactionFileList = []
    highlightDifferentialRegionsFileList = []
    highlightSignificantRegionsFileList = []

    # if args.batchMode:

    ### read hdf file
    interactionFileHDF5Object = h5py.File(args.interactionFile, 'r')
    keys_interactionFile = list(interactionFileHDF5Object.keys()) 
    resolution = interactionFileHDF5Object.attrs['resolution'][()]

    if args.differentialTestResult and args.combinationMode != 'dual':
        log.warning('Cannot use differential data, only possible for two samples in one plot.')
        exit(1)
           

    if args.combinationMode == 'dual':
        if len(keys_interactionFile) > 1:
            for i, sample in enumerate(keys_interactionFile):
                for sample2 in keys_interactionFile[i + 1:]:
                    
                    matrix_obj1 = interactionFileHDF5Object[sample]
                    matrix_obj2 = interactionFileHDF5Object[sample]

                    chromosomeList1 = sorted(list(matrix_obj1.keys()))
                    chromosomeList2 = sorted(list(matrix_obj2.keys()))
                    chromosomeList1.remove('genes')
                    chromosomeList2.remove('genes')
                    for chromosome1, chromosome2 in zip(chromosomeList1, chromosomeList2):
                        geneList1 = sorted(list(matrix_obj1[chromosome1].keys()))
                        geneList2 = sorted(list(matrix_obj2[chromosome2].keys()))

                        for gene1, gene2 in zip(geneList1, geneList2):
                            interactionFileList.append([[sample,chromosome1, gene1],[sample2,chromosome2, gene2]])

                    # for viewpoint, viewpoint2 in zip(sample, sample2):
                    #     writeFileNamesToList.append(viewpoint.encode("ascii", "ignore"))
                    #     writeFileNamesToList.append(viewpoint2.encode("ascii", "ignore"))
            # log.debug(interactionList)
            if args.differentialTestResult:
                
                differentialFileHDF5Object = h5py.File(args.differentialTestResult, 'r')
                keys_significantFile = list(differentialFileHDF5Object.keys()) 
                for plotGroup in interactionFileList:
                    differential_group = []
                    # log.debug(plotGroup)
                    # for item in plotGroup:
                        # log.debug(item)
                    if plotGroup[0][0] in keys_significantFile:
                        matrix_object = differentialFileHDF5Object[plotGroup[0][0]]
                        if plotGroup[1][0] in matrix_object:

                            matrix1_object = matrix_object[plotGroup[1][0]]
                            if plotGroup[1][1] in matrix1_object:
                                chromosome_object = matrix1_object[plotGroup[1][1]]
                        
                                if plotGroup[1][2] in chromosome_object:
                                    differential_group = [plotGroup[0][0], plotGroup[1][0], plotGroup[1][1], plotGroup[1][2], 'rejected']
                    log.debug('differential_group {}'.format(differential_group))
                    highlightDifferentialRegionsFileList.append(differential_group)

                # with open(args.differentialTestResult[0], 'r') as differentialTestFile:

                #     file_ = True
                #     while file_:
                #         file_ = differentialTestFile.readline().strip()
                #         if file_ != '':
                #             highlightDifferentialRegionsFileList.append(file_)
        else:
            log.error('Dual mode selected but only one matrix is stored')
    elif args.combinationMode == 'multi':
        if len(keys_interactionFile) > 1:
            for i, sample in enumerate(keys_interactionFile):
                for sample2 in keys_interactionFile[i + 1:]:
                    
                    matrix_obj1 = interactionFileHDF5Object[sample]
                    matrix_obj2 = interactionFileHDF5Object[sample]

                    chromosomeList1 = sorted(list(matrix_obj1.keys()))
                    chromosomeList2 = sorted(list(matrix_obj2.keys()))
                    chromosomeList1.remove('genes')
                    chromosomeList2.remove('genes')
                    for chromosome1, chromosome2 in zip(chromosomeList1, chromosomeList2):
                        geneList1 = sorted(list(matrix_obj1[chromosome1].keys()))
                        geneList2 = sorted(list(matrix_obj2[chromosome2].keys()))

                        for gene1, gene2 in zip(geneList1, geneList2):
                            interactionFileList.append([[sample,chromosome1, gene1],[sample2,chromosome2, gene2]])

                    # for viewpoint, viewpoint2 in zip(sample, sample2):
                    #     writeFileNamesToList.append(viewpoint.encode("ascii", "ignore"))
                    #     writeFileNamesToList.append(viewpoint2.encode("ascii", "ignore"))
            # log.debug(interactionList)
        else:
            log.error('Dual mode selected but only one matrix is stored')
    elif args.combinationMode == 'single':
        for i, sample in enumerate(keys_interactionFile):
                
                matrix_obj1 = interactionFileHDF5Object[sample]
                chromosomeList1 = sorted(list(matrix_obj1.keys()))
                chromosomeList1.remove('genes')
                for chromosome1 in chromosomeList1:
                    geneList1 = sorted(list(matrix_obj1[chromosome1].keys()))
                    for gene1 in geneList1:
                        interactionFileList.append([sample,chromosome1, gene1])
    elif args.combinationMode == 'oneGene': 
        if len(keys_interactionFile) > 0:
            matrix_obj1 = interactionFileHDF5Object[keys_interactionFile[0]]
            all_detected = False
            counter = 0
            gene_list = []
            while not all_detected:
                if counter == 0:
                    check_gene_name = args.combinationName
                else:
                    check_gene_name = args.combinationName + '_' + str(counter)

                if check_gene_name in matrix_obj1['genes']:
                    gene_list.append(check_gene_name)
                else:
                    all_detected = True

                counter += 1

            for gene in gene_list:
                gene_list = []
                for matrix in keys_interactionFile:
                    gene_list.append([matrix, 'genes', gene])
                interactionFileList.append(gene_list)
    
    
    # with open(args.interactionFile[0], 'r') as interactionFile:

    #     file_ = True
    #     while file_:
    #         lines = []
    #         for i in range(0, args.plotSampleNumber):
    #             file_ = interactionFile.readline().strip()
    #             if file_ != '':
    #                 lines.append(file_)
    #         interactionFileList.append(lines)
    
    # log.debug(interactionFileList[:10])
    if args.significantInteractions:
        significantFileHDF5Object = h5py.File(args.interactionFile, 'r')
        keys_significantFile = list(significantFileHDF5Object.keys()) 
        for plotGroup in interactionFileList:
            significant_group = []
            # log.debug(plotGroup)
            for item in plotGroup:
                # log.debug(item)
                if item[0] in keys_significantFile:
                    matrix_object = significantFileHDF5Object[item[0]]
                    if item[1] in matrix_object:
                        chromosome_object = matrix_object[item[1]]
                    
                        if item[2] in chromosome_object:
                            significant_group.append(item)
                        else:
                            log.warning('Requested gene {} to plot significant areas is not available in the given data {}.'.format(item[2], args.significantInteractions))
                    else:
                        log.warning('Requested chromosome {} to plot significant areas is not available in the given data {}.'.format(item[1], args.significantInteractions))

                else:
                    log.warning('Requested matrix {} to plot significant areas is not available in the given data {}.'.format(item[0], args.significantInteractions))
            highlightSignificantRegionsFileList.append(significant_group)
        # with open(args.significantInteractions[0], 'r') as significantRegionsFile:
    # log.debug(highlightSignificantRegionsFileList[:10])
    # exit(1)
        #     file_ = True
        #     while file_:
        #         lines = []
        #         for i in range(0, args.plotSampleNumber):
        #             file_ = significantRegionsFile.readline().strip()
        #             if file_ != '':
        #                 lines.append(file_)
        #         if len(lines) > 0:
        #             highlightSignificantRegionsFileList.append(lines)


    interactionFilesPerThread = len(interactionFileList) // args.threads
    highlightSignificantRegionsFileListThread = len(highlightSignificantRegionsFileList) // args.threads

    all_data_collected = False
    images_array = [None] * args.threads
    file_name_list = [None] * args.threads
    queue = [None] * args.threads
    process = [None] * args.threads
    thread_done = [False] * args.threads
    fail_flag = False
    fail_message = ''

    for i in range(args.threads):

        if i < args.threads - 1:
            interactionFileListThread = interactionFileList[i * interactionFilesPerThread:(i + 1) * interactionFilesPerThread]
            highlightDifferentialRegionsFileListThread = highlightDifferentialRegionsFileList[i * interactionFilesPerThread:(i + 1) * interactionFilesPerThread]
            highlightSignificantRegionsFileListThread = highlightSignificantRegionsFileList[i * interactionFilesPerThread:(i + 1) * interactionFilesPerThread]
        else:
            interactionFileListThread = interactionFileList[i * interactionFilesPerThread:]
            highlightDifferentialRegionsFileListThread = highlightDifferentialRegionsFileList[i * interactionFilesPerThread:]
            highlightSignificantRegionsFileListThread = highlightSignificantRegionsFileList[i * interactionFilesPerThread:]
        queue[i] = Queue()

        process[i] = Process(target=plot_images, kwargs=dict(
            pInteractionFileList=interactionFileListThread,
            pHighlightDifferentialRegionsFileList=highlightDifferentialRegionsFileListThread,
            pBackgroundData=background_data,
            pArgs=args,
            pViewpointObj=viewpointObj,
            pSignificantRegionsFileList=highlightSignificantRegionsFileListThread,
            pResolution=resolution,
            pQueue=queue[i]
        )
        )

        process[i].start()

    while not all_data_collected:
        for i in range(args.threads):
            if queue[i] is not None and not queue[i].empty():
                return_content = queue[i].get()
                if 'Fail:' in return_content:
                    fail_flag = True
                    fail_message = return_content[6:]
                else:
                    images_array[i], file_name_list[i] = return_content
                queue[i] = None
                process[i].join()
                process[i].terminate()
                process[i] = None
                thread_done[i] = True
        all_data_collected = True
        for thread in thread_done:
            if not thread:
                all_data_collected = False
        time.sleep(1)
    if fail_flag:
        log.error(fail_message)
        exit(1)

    images_array = [item for sublist in images_array for item in sublist]
    file_name_list = [item for sublist in file_name_list for item in sublist]
    
    with tarfile.open(args.outFileName, "w:gz") as tar:
        for i, bufferObject in enumerate(images_array):
            with closing(bufferObject) as fobj:
                # tarinfo = tarfile.TarInfo(filename)
                # tarinfo.size = len(fobj.getvalue())
                # tarinfo.mtime = time.time()
                # tf.addfile(tarinfo, fileobj=fobj)

                # bufferObject.seek(0)
                # data = bufferObject.read()
                # bufferObject.close()

                tar_info = tarfile.TarInfo(name=file_name_list[i] + '.' + args.outputFormat)
                tar_info.mtime=time.time()
                tar_info.size=len(fobj.getvalue())
                fobj.seek(0)
                tar.addfile(tarinfo=tar_info, fileobj=fobj)
                # tar.add(fobj)
            # f = open(file_name_list[i] + args.outputFormat, 'wb')
            # f.write(data)
            # f.close()

       
    # for name in ["foo", "bar", "quux"]:
    #     tar.add(name)
    # outFileName
    # plotsFileH5Object = h5py.File(args.outFileName, 'w')

    # for i, element in enumerate(interactionFileList):
    #     # log.debug('element: {}'.format(element))
    #     write_obj = plotsFileH5Object
    #     for matrix in element:
    #         if matrix[0] not in write_obj:
    #             write_obj = write_obj.create_group(matrix[0])
    #         else:
    #             write_obj = write_obj[matrix[0]]
        
    #     if element[0][1] not in write_obj:
    #         write_obj = write_obj.create_group(element[0][1])
    #     else:
    #         write_obj = write_obj[element[0][1]]
        
    #     if element[0][2] not in write_obj:
    #         write_obj = write_obj.create_group(element[0][2])
    #     else:
    #         write_obj = write_obj[element[0][2]]
        
    #     write_obj.create_dataset('image_raw', data=images_array[i], compression="gzip", compression_opts=9)

    # plotsFileH5Object.close()
    # else:
    #     interactionFileList = [args.interactionFile]
    #     highlightDifferentialRegionsFileList = args.differentialTestResult
    #     highlightSignificantRegionsFileList = [args.significantInteractions]
    #     plot_images(pInteractionFileList=interactionFileList,
    #                 pHighlightDifferentialRegionsFileList=highlightDifferentialRegionsFileList,
    #                 pBackgroundData=background_data,
    #                 pArgs=args,
    #                 pViewpointObj=viewpointObj,
    #                 pSignificantRegionsFileList=highlightSignificantRegionsFileList)
