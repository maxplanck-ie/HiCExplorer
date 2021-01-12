import argparse
import sys
import errno
import os
import math
from multiprocessing import Process, Queue
import time
import traceback
import logging
log = logging.getLogger(__name__)

import numpy as np
# import matplotlib
# matplotlib.use('Agg')
# import matplotlib.pyplot as plt
# import matplotlib.gridspec as gridspec
import h5py

from intervaltree import IntervalTree, Interval
import hicmatrix.HiCMatrix as hm

from hicexplorer import utilities
from hicexplorer._version import __version__
from .lib import Viewpoint


def parse_arguments(args=None):
    parser = argparse.ArgumentParser(add_help=False,
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description="""
chicAggregateStatistic is a preprocessing tool for chicDifferentialTest. It takes two consecutive viewpoint files and one target file and creates one
file containing all locations which should be tested for differential interactions. Either one target file for two consecutive viewpoint files or one
target file for all viewpoints is accepted.


An example usage is:

`$ chicAggregateStatistic --interactionFile viewpoint1.txt viewpoint2.txt --targetFile targets.txt --outFileNameSuffix aggregated.txt`

which will create a single output file: `viewpoint1_viewpoint2_aggregated.txt`

A second mode is the batch processing mode. For this you need a file containing the names of the viewpoint files (generated by chicViewpoint via --writeFileNamesToFile),
a folder which contains the files, a target list file containing the name of all target files and a folder which contains the target files (created by chicSignificantInteractions):

`$ chicAggregateStatistic --interactionFile viewpoint_names.txt --targetFile target_names.txt --interactionFileFolder viewpointFilesFolder --targetFileFolder targetFolder --batchMode --threads 20 --outFileNameSuffix aggregated.bed`

If the `--targetFileFolder` flag is not set in batch mode, it is assumed the `--targetFile` should be used for all viewpoints.
"""
                                     )
    parserRequired = parser.add_argument_group('Required arguments')

    parserRequired.add_argument('--interactionFile', '-if',
                                help='path to the interaction files which should be used for aggregation of the statistics.',
                                required=True)

    parserRequired.add_argument('--targetFile', '-tf',
                                help='path to the target files which contains the target regions to prepare data for differential analysis. This is either the target file in the hdf format created by chicSignificantInteractions or a regular, three column bed file.'
                                )

    parserOpt = parser.add_argument_group('Optional arguments')

    parserOpt.add_argument('--outFileName', '-o',
                           help='File name to save the result'
                           ' (Default: %(default)s).',
                           required=False,
                           default='aggregate_target.hdf')

    # parserOpt.add_argument('--interactionFileFolder', '-iff',
    #                        help='Folder where the interaction files are stored. Applies only for batch mode'
    #                        ' (Default: %(default)s).',
    #                        required=False,
    #                        default='.')
    # parserOpt.add_argument('--targetFileFolder', '-tff',
    #                        help='Folder where the target files are stored. Applies only for batch mode.',
    #                        required=False)
    # parserOpt.add_argument('--outputFolder', '-o',
    #                        help='Output folder containing the files'
    #                        ' (Default: %(default)s).',
    #                        required=False,
    #                        default='aggregatedFiles')
    # parserOpt.add_argument('--writeFileNamesToFile', '-w',
    #                        help='(Default: %(default)s).',
    #                        default='aggregatedFilesBatch.txt')
    # parserOpt.add_argument('--batchMode', '-bm',
    #                        help='turns on batch mode. The files provided by --interactionFile and/or --targetFile contain a list of the files to be processed.',
    #                        required=False,
    #                        action='store_true')

    parserOpt.add_argument('--threads', '-t',
                           help='Number of threads (uses the python multiprocessing module)ist'
                           ' (Default: %(default)s).',
                           required=False,
                           default=4,
                           type=int
                           )

    parserOpt.add_argument("--help", "-h", action="help",
                           help="show this help message and exit")

    parserOpt.add_argument('--version', action='version',
                           version='%(prog)s {}'.format(__version__))
    return parser


def filter_scores_target_list(pScoresDictionary, pTargetList=None, pTargetIntervalTree=None, pTargetFile=None):

    accepted_scores = {}
    same_target_dict = {}
    target_regions_intervaltree = None
    if pTargetList is not None:

        # target_regions = utilities.readBed(pTargetList)
        # read hdf content for this specific combination
        targetFileHDF5Object = h5py.File(pTargetFile, 'r')
        target_object = targetFileHDF5Object['/'.join(pTargetList)]
        chromosome = target_object.get('chromosome')[()].decode("utf-8") 
        start_list = list(target_object['start_list'][:])
        end_list = list(target_object['end_list'][:])

        chromosome = [chromosome] * len(start_list)

        target_regions = list(zip(chromosome, start_list, end_list))
        # log.debug('target_regions {}'.format(target_regions))
        if len(target_regions) == 0:
            return accepted_scores

        hicmatrix = hm.hiCMatrix()
        target_regions_intervaltree = hicmatrix.intervalListToIntervalTree(target_regions)[0]
        # log.debug('target_regions_intervaltree {}'.format(target_regions_intervaltree))

    elif pTargetIntervalTree is not None:
        target_regions_intervaltree = pTargetIntervalTree
    else:
        log.error('No target list given.')
        raise Exception('No target list given.')
    for key in pScoresDictionary:
        # try:
        chromosome = pScoresDictionary[key][0]
        start = int(pScoresDictionary[key][1])
        end = int(pScoresDictionary[key][2])
        if chromosome in target_regions_intervaltree:
            target_interval = target_regions_intervaltree[chromosome][start:end]
        else:
            continue
        if target_interval:
            target_interval = sorted(target_interval)[0]
            if target_interval in same_target_dict:
                same_target_dict[target_interval].append(key)
            else:
                same_target_dict[target_interval] = [key]

    for target in same_target_dict:

        values = np.array([0.0, 0.0, 0.0])
        same_target_dict[target] = sorted(same_target_dict[target])

        for key in same_target_dict[target]:
            values += np.array(list(map(float, pScoresDictionary[key][-3:])))
        new_data_line = pScoresDictionary[same_target_dict[target][0]]
        new_data_line[2] = pScoresDictionary[same_target_dict[target][-1]][2]
        new_data_line[-5] = pScoresDictionary[same_target_dict[target][-1]][-5]
        new_data_line[-3] = values[0]
        new_data_line[-2] = values[1]
        new_data_line[-1] = values[2]

        accepted_scores[same_target_dict[target][0]] = new_data_line

    return accepted_scores


# def write(pOutFileName, pHeader, pNeighborhoods, pInteractionLines):

#     with open(pOutFileName, 'w') as file:
#         file.write('# Aggregated file, created with HiCExplorer\'s chicAggregateStatistic version {}\n'.format(__version__))
#         file.write(pHeader)
#         # file.write(
#         #     '#Chromosome\tStart\tEnd\tGene\tSum of interactions\tRelative distance\tRelative Interactions\tp-value\tx-fold\tRaw target')
#         file.write(
#             '#Chromosome\tStart\tEnd\tGene\tSum of interactions\tRelative distance\tRaw target')
#         file.write('\n')

#         if pNeighborhoods is not None:
#             for data in pNeighborhoods:
#                 log.debug('pInteractionLines[data]: {}'.format(pInteractionLines[data]))
#                 log.debug('pInteractionLines[data][:-6]: {}'.format(pInteractionLines[data][:6]))

#                 new_line = '\t'.join(pInteractionLines[data][:6])
#                 new_line += '\t' + format(pNeighborhoods[data][-1], '10.5f')
#                 new_line += '\n'
#                 file.write(new_line)

def writeAggregateHDF(pOutFileName, pOutfileNamesList, pAcceptedScoresList):
    # Chromosome	Start	End	Gene	Sum of interactions	Relative distance	Raw target

    aggregateFileH5Object = h5py.File(pOutFileName, 'w')
    counter = 0
    log.debug('key_outer {}'.format(pOutfileNamesList[:2]))
    for key_outer, data_outer in zip(pOutfileNamesList, pAcceptedScoresList):

        matrix_combination_key = key_outer[0][0] + '_' + key_outer[1][0]
        # log.debug('matrix_combination_key {}'.format(matrix_combination_key))
        if matrix_combination_key not in aggregateFileH5Object:
            matrixCombinationGroup = aggregateFileH5Object.create_group(matrix_combination_key)
        else:
            matrixCombinationGroup = aggregateFileH5Object[matrix_combination_key]

        for key, data in zip(key_outer, data_outer):
            if len(data) == 0:
                continue
            else:
                counter += 1
            chromosome = None
            start_list = []
            end_list = []
            gene_name = None
            sum_of_interactions = None
            relative_distance_list = []
        
            raw_target_list = [] 

            # log.debug('data {}'.format(data))
            for key_accepted in data:
                # log.debug('datum {}'.format(data[key_accepted]))
                # log.debug('interactionData {}'.format(data[1][key_accepted]))

                chromosome = data[key_accepted][0]
                start_list.append(data[key_accepted][1])
                end_list.append(data[key_accepted][2])
                gene_name = data[key_accepted][3]
                sum_of_interactions = data[key_accepted][4]
                relative_distance_list.append(data[key_accepted][5])
                raw_target_list.append(data[key_accepted][-1])

            if key[0] not in matrixCombinationGroup:
                matrixGroup = matrixCombinationGroup.create_group(key[0])
            else:
                matrixGroup = matrixCombinationGroup[key[0]]
            if key[1] not in matrixGroup:
                chromosomeObject = matrixGroup.create_group(key[1])
            else:
                chromosomeObject = matrixGroup[chromosome]

            if 'genes' not in matrixGroup:
                geneGroup = matrixGroup.create_group('genes')
            else:
                geneGroup = matrixGroup['genes']

            groupObject = chromosomeObject.create_group(key[-1])
            groupObject["chromosome"] = chromosome
            groupObject.create_dataset("start_list", data=start_list, compression="gzip", compression_opts=9)
            groupObject.create_dataset("end_list", data=end_list, compression="gzip", compression_opts=9)
            groupObject["gene_name"] = gene_name
            groupObject["sum_of_interactions"] = sum_of_interactions
            groupObject.create_dataset("relative_distance_list", data=relative_distance_list, compression="gzip", compression_opts=9)
            groupObject.create_dataset("raw_target_list", data=raw_target_list, compression="gzip", compression_opts=9)



            # group_name = pViewpointObj.writeInteractionFileHDF5(
            #             chromosomeObject, key[2], [chromosome, start_list, end_list, gene_name, sum_of_interactions, relative_distance_list,
            #                                         relative_interactions_list, pvalue_list, xfold_list, raw_target_list])

            geneGroup[key[-1]] = chromosomeObject[key[-1]]
    
    log.debug('counter {}'.format(counter))
    aggregateFileH5Object.close()


def run_target_list_compilation(pInteractionFilesList, pTargetList, pArgs, pViewpointObj, pQueue=None, pOneTarget=False):
    outfile_names_list = []
    accepted_scores_list = []

    target_regions_intervaltree = None
    # log.debug('size: interactionFileList: {} '.format(pInteractionFilesList))
    # log.debug('size: pTargetList: {} '.format(pTargetList))
    # log.debug('pOneTarget: {} '.format(pOneTarget))

    try:
        if pOneTarget == True:
            target_regions = utilities.readBed(pTargetList)
            hicmatrix = hm.hiCMatrix()
            target_regions_intervaltree = hicmatrix.intervalListToIntervalTree(target_regions)[0]

        for i, interactionFile in enumerate(pInteractionFilesList):
            outfile_names_list_intern = []
            accepted_scores_list_intern = []
            for sample in interactionFile:
                # sample_prefix = []
                # if pArgs.interactionFileFolder != '.':
                #     absolute_sample_path = pArgs.interactionFileFolder + '/' + sample
                # else:
                #     absolute_sample_path = sample
                interaction_data, interaction_file_data = pViewpointObj.readInteractionFileForAggregateStatistics(pArgs.interactionFile, sample)
                # log.debug('len(pTargetList) {}'.format(len(pTargetList)))
                if pOneTarget == True:
                    target_file = None
                    
                    # log.debug('197')

                else:
                    target_file = pTargetList[i]

                    # log.debug('201')

                accepted_scores = filter_scores_target_list(interaction_file_data, pTargetList=target_file, pTargetIntervalTree=target_regions_intervaltree, pTargetFile=pArgs.targetFile)

                if len(accepted_scores) == 0:
                    # do not call 'break' or 'continue'
                    # with this an empty file is written and no track of 'no significant interactions' detected files needs to be recorded.
                    # if pArgs.batchMode:
                    #     with open('errorLog.txt', 'a+') as errorlog:
                    #         errorlog.write('Failed for: {} and {}.\n'.format(interactionFile[0], interactionFile[1]))
                    # else:
                    #     log.info('No target regions found')
                    pass
                # sample_prefix.append(sample[0])
                # sample_prefix.append(sample[1])
                # sample_prefix.append(sample[2])
                outfile_names_list_intern.append(sample)
                accepted_scores_list_intern.append(accepted_scores)
            outfile_names_list.append(outfile_names_list_intern)
            accepted_scores_list.append(accepted_scores_list_intern)
                # outFileName = '.'.join(sample.split('/')[-1].split('.')[:-1]) + '_' + pArgs.outFileNameSuffix

                # if pArgs.batchMode:
                #     outfile_names.append(outFileName)
                # if pArgs.outputFolder != '.':
                #     outFileName = pArgs.outputFolder + '/' + outFileName

                # write(outFileName, header, accepted_scores,
                #       interaction_file_data)
    except Exception as exp:
        pQueue.put('Fail: ' + str(exp) + traceback.format_exc())
        return
    if pQueue is None:
        return
    pQueue.put([outfile_names_list, accepted_scores_list])
    return


def call_multi_core(pInteractionFilesList, pTargetFileList, pFunctionName, pArgs, pViewpointObj):
    if len(pInteractionFilesList) < pArgs.threads:
        pArgs.threads = len(pInteractionFilesList)
    outfile_names_list = [None] * pArgs.threads
    accepted_scores_list = [None] * pArgs.threads

    interactionFilesPerThread = len(pInteractionFilesList) // pArgs.threads

    all_data_collected = False
    queue = [None] * pArgs.threads
    process = [None] * pArgs.threads
    thread_done = [False] * pArgs.threads
    one_target = True if len(pTargetFileList) == 1 else False
    fail_flag = False
    fail_message = ''
    for i in range(pArgs.threads):

        if i < pArgs.threads - 1:
            interactionFileListThread = pInteractionFilesList[i * interactionFilesPerThread:(i + 1) * interactionFilesPerThread]
            if len(pTargetFileList) == 1:
                targetFileListThread = pTargetFileList[0]
            else:
                targetFileListThread = pTargetFileList[i * interactionFilesPerThread:(i + 1) * interactionFilesPerThread]
        else:
            interactionFileListThread = pInteractionFilesList[i * interactionFilesPerThread:]
            if len(pTargetFileList) == 1:
                targetFileListThread = pTargetFileList[0]
            else:
                targetFileListThread = pTargetFileList[i * interactionFilesPerThread:]

        queue[i] = Queue()
        process[i] = Process(target=pFunctionName, kwargs=dict(
            pInteractionFilesList=interactionFileListThread,
            pTargetList=targetFileListThread,
            pArgs=pArgs,
            pViewpointObj=pViewpointObj,
            pQueue=queue[i],
            pOneTarget=one_target
        )
        )

        process[i].start()

    while not all_data_collected:
        for i in range(pArgs.threads):
            if queue[i] is not None and not queue[i].empty():
                background_data_thread = queue[i].get()
                if 'Fail:' in background_data_thread:
                    fail_flag = True
                    fail_message = background_data_thread[6:]
                else:
                    outfile_names_list[i], accepted_scores_list[i] = background_data_thread
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
    outfile_names_list = [item for sublist in outfile_names_list for item in sublist]
    accepted_scores_list = [item for sublist in accepted_scores_list for item in sublist]

    return outfile_names_list, accepted_scores_list


def main(args=None):
    args = parse_arguments().parse_args(args)
    viewpointObj = Viewpoint()
    outfile_names = []
    # if not os.path.exists(args.outputFolder):
    #     try:
    #         os.makedirs(args.outputFolder)
    #     except OSError as exc:  # Guard against race condition
    #         if exc.errno != errno.EEXIST:
    #             raise

    interactionList = []
    targetList = []
    present_genes = {}
    ### read hdf file
    interactionFileHDF5Object = h5py.File(args.interactionFile, 'r')
    keys_interactionFile = list(interactionFileHDF5Object.keys()) 

    if h5py.is_hdf5(args.targetFile):
        targetFileHDF5Object = h5py.File(args.targetFile, 'r')
        keys_targetFile = list(targetFileHDF5Object.keys())
        log.debug('keys_interactionFile {}'.format(keys_targetFile))
        for outer_matrix in keys_targetFile:
            if outer_matrix not in present_genes:
                present_genes[outer_matrix] = {}
            inner_matrix_object = targetFileHDF5Object[outer_matrix]
            keys_inner_matrices = list(inner_matrix_object.keys())
            for inner_matrix in keys_inner_matrices:
                if inner_matrix not in present_genes[outer_matrix]:
                    present_genes[outer_matrix][inner_matrix] = []
                inner_object = inner_matrix_object[inner_matrix]
                gene_object = inner_object['genes']
                keys_genes = list(gene_object.keys())
                for gen in keys_genes:
                    targetList.append([outer_matrix, inner_matrix, 'genes', gen])
                    present_genes[outer_matrix][inner_matrix].append(gen)

    else:
        targetList = [args.targetFile]

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
                        if gene1 in present_genes[sample][sample2]:
                            interactionList.append([[sample,chromosome1, gene1],[sample2,chromosome2, gene2]])

                # for viewpoint, viewpoint2 in zip(sample, sample2):
                #     writeFileNamesToList.append(viewpoint.encode("ascii", "ignore"))
                #     writeFileNamesToList.append(viewpoint2.encode("ascii", "ignore"))
        # log.debug(interactionList)
    else:
        log.error('To aggregate and prepare the data for the differential test, at least two matrices need to be stored, but only one is present.')
    interactionFileHDF5Object.close()

    

    # log.debug(targetList)
    outfile_names_list, accepted_scores_list = call_multi_core(interactionList, targetList, run_target_list_compilation, args, viewpointObj)

    writeAggregateHDF(args.outFileName, outfile_names_list, accepted_scores_list)
    # log.debug('len(interactionList) {}'.format(len(interactionList)))
    # log.debug('len(targetList) {}'.format(len(targetList) ))

    # log.debug(outfile_names_list)
    # log.debug(accepted_scores_list)

    # if args.batchMode:
    #     with open(args.interactionFile[0], 'r') as interactionFile:
    #         file_ = True
    #         while file_:
    #             file_ = interactionFile.readline().strip()
    #             file2_ = interactionFile.readline().strip()
    #             if file_ != '' and file2_ != '':
    #                 interactionFileList.append((file_, file2_))

    #     if len(args.targetFile) == 1 and args.targetFileFolder:

    #         with open(args.targetFile[0], 'r') as targetFile:
    #             file_ = True
    #             while file_:
    #                 file_ = targetFile.readline().strip()
    #                 if file_ != '':
    #                     targetFileList.append(file_)
    #     else:
    #         targetFileList = args.targetFile
    #     outfile_names = call_multi_core(interactionFileList, targetFileList, run_target_list_compilation, args, viewpointObj)

    # else:
    #     targetFileList = args.targetFile
    #     if len(args.interactionFile) % 2 == 0:
    #         i = 0
    #         while i < len(args.interactionFile):
    #             interactionFileList.append(
    #                 (args.interactionFile[i], args.interactionFile[i + 1]))
    #             i += 2
    #     else:
    #         log.error('Number of interaction files needs to be even: {}'.format(
    #             len(args.interactionFile)))
    #         exit(1)
    #     run_target_list_compilation(interactionFileList, targetFileList, args, viewpointObj)

    # if args.batchMode:
    #     with open(args.writeFileNamesToFile, 'w') as nameListFile:
    #         nameListFile.write('\n'.join(outfile_names))
