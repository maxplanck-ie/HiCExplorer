import argparse
import sys
import errno
import os
import math
from multiprocessing import Process, Queue
import time
import logging
log = logging.getLogger(__name__)

import numpy as np
from scipy import stats
import h5py

import hicmatrix.HiCMatrix as hm
from hicexplorer import utilities
from hicexplorer._version import __version__
from .lib import Viewpoint

def parse_arguments(args=None):
    parser = argparse.ArgumentParser(add_help=False,
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     description="""
chicDifferentialTest tests if two locations under consideration of the reference point have a different interaction count. For this either Fisher's test or the chi2 contingency test can be used.
The files that are accepted for this test can be created with `chicAggregateStatistic`. H0 assumes the interactions are not different. Therefore the differential interaction counts are all where H0 was rejected.


An example usage is:

`$ chicDifferentialTest --interactionFile viewpoint1_aggregated.txt  viewpoint2_aggregated.txt --alpha 0.05 --statisticTest fisher --outputFolder differentialResults`

and this will create three files: `viewpoint1_viewpoint2_aggregated_H0_accepted.txt`, `viewpoint1_viewpoint2_aggregated_H0_rejected.txt`, `viewpoint1_viewpoint2_aggregated_results.txt`

The first file contains all locations where H0 was accepted, the second file all locations where H0 was rejected and the third one all locations with the test result.



A second mode is the batch processing mode. For this you need a file containing the names of the aggregated files (generated by chicAggregateStatistic via --writeFileNamesToFile and the batch mode):

`$ chicDifferentialTest --statisticTest fisher --alpha 0.05 --interactionFile aggregatedFilesBatch.txt --interactionFileFolder aggregatedFilesFolder --batchMode --threads 20 --outputFolder differentialResults`

This will create, as in the non-batch mode, three files per aggregated file and writes the file name to the file given by `--rejectedFileNamesToFile`. This last file can be used to plot the differential interactions per viewpoint in batch mode, using chicPlotViewpoint.
"""
                                     )

    parserRequired = parser.add_argument_group('Required arguments')

    parserRequired.add_argument('--aggregatedFile', '-if',
                                help='path to the aggregated files which should be used for the differential test.',
                                required=True)

    parserRequired.add_argument('--alpha', '-a',
                                help='define a significance level (alpha) for accepting samples',
                                type=float,
                                required=True)

    parserOpt = parser.add_argument_group('Optional arguments')

    # parserOpt.add_argument('--interactionFileFolder', '-iff',
    #                        help='Folder where the interaction files are stored. Applies only for batch mode'
    #                        ' (Default: %(default)s).',
    #                        required=False,
    #                        default='.')
    parserOpt.add_argument('--outFileName', '-o',
                           help='Output file for the differential results'
                           ' (Default: %(default)s).',
                           required=False,
                           default='differentialResults.hdf5')
    parserOpt.add_argument('--statisticTest',
                           help='Type of test used: fisher\'s exact test or chi2 contingency'
                           ' (Default: %(default)s).',
                           choices=['fisher', 'chi2'],
                           default='fisher')
    # parserOpt.add_argument('--batchMode', '-bm',
    #                        help='turn on batch mode. The given file for --interactionFile and or --targetFile contain a list of the to be processed files.',
    #                        required=False,
    #                        action='store_true')
    parserOpt.add_argument('--threads', '-t',
                           help='Number of threads (uses the python multiprocessing module)'
                           ' (Default: %(default)s).',
                           required=False,
                           default=4,
                           type=int
                           )
    # parserOpt.add_argument('--rejectedFileNamesToFile', '-r',
    #                        help='Writes the names of the rejected H0 (therefore containing the differential interactions) to file. Can be used for batch processing mode of chicPlotViewpoint.'
    #                        ' (Default: %(default)s).',
    #                        default='rejected_H0.txt')
    parserOpt.add_argument("--help", "-h", action="help",
                           help="show this help message and exit")
    parserOpt.add_argument('--version', action='version',
                           version='%(prog)s {}'.format(__version__))
    return parser


def readInteractionFile(pInteractionFile):

    line_content = []
    data = []

    with open(pInteractionFile, 'r') as file:
        file.readline()
        header = file.readline()
        sum_of_all_interactions = float(
            header.strip().split('\t')[-1].split(' ')[-1])
        header += file.readline()
        for line in file.readlines():
            if line.startswith('#'):
                continue
            _line = line.strip().split('\t')
            if len(_line) <= 1:
                continue
            line_content.append(_line)
            data.append([sum_of_all_interactions, float(_line[-1])])

    return header, line_content, data


def chisquare_test(pDataFile1, pDataFile2, pAlpha):
    # pair of accepted/unaccepted and pvalue
    # True is rejection of H0
    # False acceptance of H0
    test_result = []
    accepted = []
    rejected = []
    # Find the critical value for alpha confidence level
    critical_value = stats.chi2.ppf(q=1 - pAlpha, df=1)
    zero_values_counter = 0
    for i, (group1, group2) in enumerate(zip(pDataFile1, pDataFile2)):
        try:
            chi2, p_value, dof, ex = stats.chi2_contingency(
                [group1, group2], correction=False)
            if chi2 >= critical_value:
                test_result.append(p_value)
                rejected.append([i, p_value])
            else:
                test_result.append(p_value)
                accepted.append([i, p_value])

        except ValueError:
            zero_values_counter += 1
            test_result.append(np.nan)
            accepted.append([i, 1.0])

    if zero_values_counter > 0:
        log.info('{} samples were not tested because at least one condition contained no data in both groups.'.format(
            zero_values_counter))
    return test_result, accepted, rejected


def fisher_exact_test(pDataFile1, pDataFile2, pAlpha):

    test_result = []
    accepted = []
    rejected = []
    for i, (group1, group2) in enumerate(zip(pDataFile1, pDataFile2)):
        try:
            odds, p_value = stats.fisher_exact(np.ceil([group1, group2]))
            if p_value <= pAlpha:
                test_result.append(p_value)
                rejected.append([i, p_value])
            else:
                test_result.append(p_value)
                accepted.append([i, p_value])
        except ValueError:
            test_result.append(np.nan)
            accepted.append([i, 1.0])
    return test_result, accepted, rejected


def writeResult(pOutFileName, pData, pHeaderOld, pHeaderNew, pAlpha, pTest):

    with open(pOutFileName, 'w') as file:
        header = '# Differential analysis result file of HiCExplorer\'s chicDifferentialTest version '
        header += str(__version__)
        header += '\n'

        header += '# This file contains the p-values computed by {} test\n'.format(
            pTest)
        header += '# To test the smoothed (float) values they were rounded up to the next integer\n'
        header += '#\n'

        header += ' '.join(['# Alpha level', str(pAlpha)])
        header += '\n'
        header += ' '.join(['# Degrees of freedom', '1'])
        header += '\n#\n'

        file.write(header)

        file.write(pHeaderOld.split('\n')[0] + '\n')
        file.write(pHeaderNew.split('\n')[0] + '\n')

        file.write('#Chromosome\tStart\tEnd\tGene\tRelative distance\tsum of interactions 1\ttarget_1 raw\tsum of interactions 2\ttarget_2 raw\tp-value\n')

        if pData:
            for data in pData:
                line = '\t'.join(data[0][:4])
                line += '\t'

                line += '{}'.format(data[0][5])
                line += '\t'
                line += '\t'.join(format(x, '.5f') for x in data[3])
                line += '\t'

                line += '\t'.join(format(x, '.5f') for x in data[4])
                line += '\t'

                line += '\t{}\n'.format(format(data[2], '.5f'))
                file.write(line)

def writeResultHDF(pOutFileName, pAcceptedData, pRejectedData, pAllResultData, pInputData, pAlpha, pTest):
    resultFileH5Object = h5py.File(pOutFileName, 'w')
    resultFileH5Object.attrs.create('alpha', pAlpha, dtype='f')
    # resultFileH5Object.attrs.create('statistic test', pTest, dtype='S')
    # resultFileH5Object.attrs.create('version', __version__, dtype='f')

    # log.debug('pInputData[:1] {}'.format(pInputData[:2]))
    # log.debug('pAcceptedData[:1] {}'.format(pAcceptedData[:2]))
    # log.debug('pRejectedData[:1] {}'.format(pRejectedData[:2]))
    # log.debug('pAllResultData[:1] {}'.format(pAllResultData[:2]))

    all_data_dict = {'accepted' : pAcceptedData, 'rejected' : pRejectedData, 'all' : pAllResultData}
    for i, inputData in enumerate(pInputData):
        log.debug(inputData)
        matrix1_name = inputData[0][1]
        matrix2_name = inputData[1][1]
        chromosome = inputData[0][2]
        gene_name = inputData[0][3]


        if matrix1_name not in resultFileH5Object:
            matrix1_object = resultFileH5Object.create_group(matrix1_name)
        else:
            matrix1_object = resultFileH5Object[matrix1_name]
        
        if matrix2_name not in matrix1_object:
            matrix2_object = matrix1_object.create_group(matrix2_name)
        else:
            matrix2_object = matrix1_object[matrix2_name]

        if chromosome not in matrix2_object:
            chromosome_object = matrix2_object.create_group(chromosome)
        else:
            chromosome_object = matrix2_object[chromosome]

        # if chromosome not in matrix2_obj:
        gene_object = chromosome_object.create_group(gene_name)
        accepted_object = gene_object.create_group('accepted')
        rejected_object = gene_object.create_group('rejected')
        all_object = gene_object.create_group('all')

# [chromosome, start_list[i], end_list[i], gene_name, sum_of_interactions, relative_distance_list[i], raw_target_list[i]])
       
        for category in ['accepted', 'rejected', 'all']:
            write_object = gene_object[category]
            data_object = all_data_dict[category][i]
            if len(data_object) == 0:
                continue
            chromosome = None
            start_list = []
            end_list = []
            # gene_name = None
            sum_of_interactions_1 = None
            sum_of_interactions_2 = None

            relative_distance_list = []
            pvalue_list = []
        
            raw_target_list_1 = [] 
            raw_target_list_2 = [] 


            # log.debug('data {}'.format(data))
            for data in data_object:
                
                # log.debug('datum {}'.format(data[key_accepted]))
                # log.debug('interactionData {}'.format(data[1][key_accepted]))

                chromosome = data[0][0]
                start_list.append(data[0][1])
                end_list.append(data[0][2])
                # gene_name = data[0][3]
                log.debug('gene_name {}'.format(gene_name))
                log.debug('data {}'.format(data))

                relative_distance_list.append(data[0][5])


                sum_of_interactions_1 = float(data[3][0])
                sum_of_interactions_2 = float(data[4][0])


                raw_target_list_1.append(data[3][1])
                raw_target_list_2.append(data[4][1])
                pvalue_list.append(data[2])


            write_object["chromosome"] = str(chromosome)
            write_object.create_dataset("start_list", data=start_list, compression="gzip", compression_opts=9)
            write_object.create_dataset("end_list", data=end_list, compression="gzip", compression_opts=9)
            write_object["gene"] = str(gene_name)
            write_object.create_dataset("relative_distance_list", data=relative_distance_list, compression="gzip", compression_opts=9)
            
            # write_object.create_dataset("sum_of_interactions_1", data=sum_of_interactions_1)
            # write_object.create_dataset("sum_of_interactions_2", data=sum_of_interactions_2)


            write_object["sum_of_interactions_1"] = float(sum_of_interactions_1)
            write_object["sum_of_interactions_2"] = float(sum_of_interactions_2)

            
            write_object.create_dataset("raw_target_list_1", data=raw_target_list_1, compression="gzip", compression_opts=9)
            write_object.create_dataset("raw_target_list_2", data=raw_target_list_2, compression="gzip", compression_opts=9)
            write_object.create_dataset("pvalue_list", data=pvalue_list, compression="gzip", compression_opts=9)


    resultFileH5Object.close()


def run_statistical_tests(pInteractionFilesList, pArgs, pViewpointObject, pQueue=None):
    rejected_names = []
    accepted_list = []
    rejected_list = []
    all_results_list = []
    try:
        for interactionFile in pInteractionFilesList:

            # sample_prefix = interactionFile[0].split(
            #     '/')[-1].split('_')[0] + '_' + interactionFile[1].split('/')[-1].split('_')[0]

            # region_prefix = '_'.join(
            #     interactionFile[0].split('/')[-1].split('_')[1:6])

            # outFileName = sample_prefix + '_' + region_prefix
            # rejected_name_output_file = outFileName + '_H0_rejected.txt'

            # if pArgs.outputFolder != '.':
            #     outFileName_accepted = pArgs.outputFolder + \
            #         '/' + outFileName + '_H0_accepted.txt'
            #     outFileName_rejected = pArgs.outputFolder + \
            #         '/' + outFileName + '_H0_rejected.txt'
            #     outFileName = pArgs.outputFolder + '/' + outFileName + '_results.txt'
            # else:
            #     outFileName_accepted = outFileName + '_H0_accepted.txt'
            #     outFileName_rejected = outFileName + '_H0_rejected.txt'
            #     outFileName = outFileName + '_results.txt'

            # if pArgs.interactionFileFolder != '.':
            #     absolute_sample_path1 = pArgs.interactionFileFolder + '/' + interactionFile[0]
            #     absolute_sample_path2 = pArgs.interactionFileFolder + '/' + interactionFile[1]

            # else:
            #     absolute_sample_path1 = interactionFile[0]
            #     absolute_sample_path2 = interactionFile[1]

            line_content1, data1 = pViewpointObject.readAggregatedFileHDF(pArgs.aggregatedFile, interactionFile[0])
            line_content2, data2 = pViewpointObject.readAggregatedFileHDF(pArgs.aggregatedFile, interactionFile[1])

            if len(line_content1) == 0 or len(line_content2) == 0:
                # writeResult(outFileName, None, header1, header2,
                #             pArgs.alpha, pArgs.statisticTest)
                # writeResult(outFileName_accepted, None, header1, header2,
                #             pArgs.alpha, pArgs.statisticTest)
                # writeResult(outFileName_rejected, None, header1, header2,
                #             pArgs.alpha, pArgs.statisticTest)
                # rejected_names.append(rejected_name_output_file)
                continue
            if pArgs.statisticTest == 'chi2':
                test_result, accepted, rejected = chisquare_test(
                    data1, data2, pArgs.alpha)
            elif pArgs.statisticTest == 'fisher':
                test_result, accepted, rejected = fisher_exact_test(
                    data1, data2, pArgs.alpha)

            write_out_lines = []
            for i, result in enumerate(test_result):
                write_out_lines.append(
                    [line_content1[i], line_content2[i], result, data1[i], data2[i]])

            write_out_lines_accepted = []
            for result in accepted:
                write_out_lines_accepted.append(
                    [line_content1[result[0]], line_content2[result[0]], result[1], data1[result[0]], data2[result[0]]])

            write_out_lines_rejected = []
            for result in rejected:
                write_out_lines_rejected.append(
                    [line_content1[result[0]], line_content2[result[0]], result[1], data1[result[0]], data2[result[0] ] ] )
                log.debug('foo: {}'.format([line_content1[result[0]], line_content2[result[0]], result[1], data1[result[0]], data2[result[0] ] ]))
            # log.debug('write_out_lines_rejected {}'.format(write_out_lines_rejected))
                

            accepted_list.append(write_out_lines_accepted)
            rejected_list.append(write_out_lines_rejected)
            all_results_list.append(write_out_lines)
            # writeResult(outFileName, write_out_lines, header1, header2,
            #             pArgs.alpha, pArgs.statisticTest)
            # writeResult(outFileName_accepted, write_out_lines_accepted, header1, header2,
            #             pArgs.alpha, pArgs.statisticTest)
            # writeResult(outFileName_rejected, write_out_lines_rejected, header1, header2,
            #             pArgs.alpha, pArgs.statisticTest)
            # rejected_names.append(rejected_name_output_file)

        log.debug('rejected 415 {}'.format(rejected_list))
    
    except Exception as exp:
        pQueue.put('Fail: ' + str(exp))
        return

    if pQueue is None:
        return
    pQueue.put([accepted_list, rejected_list, all_results_list])
    return


def main(args=None):
    args = parse_arguments().parse_args(args)
    # if not os.path.exists(args.outputFolder):
    #     try:
    #         os.makedirs(args.outputFolder)
    #     except OSError as exc:  # Guard against race condition
    #         if exc.errno != errno.EEXIST:
    #             raise

    viewpointObj = Viewpoint()

    aggregatedList = []


    aggregatedFileHDF5Object = h5py.File(args.aggregatedFile, 'r')
    keys_aggregatedFile = list(aggregatedFileHDF5Object.keys()) 
    log.debug('keys_aggregatedFile {}'.format(keys_aggregatedFile))

    for i, combinationOfMatrix in enumerate(keys_aggregatedFile):
        # log.debug('list(aggregatedFileHDF5Object[combinationOfMatrix].keys()) {}'.format(list(aggregatedFileHDF5Object[combinationOfMatrix].keys())))
        keys_matrix_intern = list(aggregatedFileHDF5Object[combinationOfMatrix].keys())
        if len(keys_matrix_intern) == 0:
            continue
    # if len(keys_aggregatedFile) > 1:

        log.debug('combinationOfMatrix {} keys_matrix_intern {}'.format(combinationOfMatrix, keys_matrix_intern))
        matrix1 = keys_matrix_intern[0]
        matrix2 = keys_matrix_intern[1]

        matrix_obj1 = aggregatedFileHDF5Object[combinationOfMatrix + '/' + matrix1]
        matrix_obj2 = aggregatedFileHDF5Object[combinationOfMatrix + '/' + matrix2]
        # for 
        # for sample2 in keys_aggregatedFile[i + 1:]:
            
        #     matrix_obj1 = aggregatedFileHDF5Object[sample]
        #     matrix_obj2 = aggregatedFileHDF5Object[sample]

        chromosomeList1 = sorted(list(matrix_obj1.keys()))
        chromosomeList2 = sorted(list(matrix_obj2.keys()))
        chromosomeList1.remove('genes')
        chromosomeList2.remove('genes')
        for chromosome1, chromosome2 in zip(chromosomeList1, chromosomeList2):
            geneList1 = sorted(list(matrix_obj1[chromosome1].keys()))
            geneList2 = sorted(list(matrix_obj2[chromosome2].keys()))

            for gene1, gene2 in zip(geneList1, geneList2):
                # if gene1 in present_genes[sample][sample2]:
                aggregatedList.append([[combinationOfMatrix, matrix1, chromosome1, gene1],[combinationOfMatrix, matrix2, chromosome2, gene2]])

        # for viewpoint, viewpoint2 in zip(sample, sample2):
        #     writeFileNamesToList.append(viewpoint.encode("ascii", "ignore"))
            #     writeFileNamesToList.append(viewpoint2.encode("ascii", "ignore"))
    # log.debug(interactionList)
        # else:
        #     log.error('To aggregate and prepare the data for the differential test, at least two matrices need to be stored, but only one is present.')
    aggregatedFileHDF5Object.close()

    log.debug('aggregatedList {}'.format(aggregatedList))
    
    # if args.batchMode:
    #     with open(args.interactionFile[0], 'r') as interactionFile:
    #         file_ = True
    #         while file_:
    #             # for line in fh.readlines():
    #             file_ = interactionFile.readline().strip()
    #             file2_ = interactionFile.readline().strip()
    #             if file_ != '' and file2_ != '':
    #                 interactionFileList.append((file_, file2_))
    #         log.debug('len(interactionFileList) {}'.format(len(interactionFileList)))
    # else:
    #     if len(args.interactionFile) % 2 == 0:

    #         i = 0
    #         while i < len(args.interactionFile):
    #             interactionFileList.append(
    #                 (args.interactionFile[i], args.interactionFile[i + 1]))
    #             i += 2

    fail_flag = False
    fail_message = ''
    # if args.batchMode:
    all_data = [None] * args.threads
    accepted_data = [None] * args.threads
    rejected_data = [None] * args.threads

    aggregatedListPerThread = len(aggregatedList) // args.threads
    all_data_collected = False
    queue = [None] * args.threads
    process = [None] * args.threads
    thread_done = [False] * args.threads
    length_of_threads = 0
    for i in range(args.threads):

        if i < args.threads - 1:
            aggregatedListThread = aggregatedList[i * aggregatedListPerThread:(i + 1) * aggregatedListPerThread]
        else:
            aggregatedListThread = aggregatedList[i * aggregatedListPerThread:]
        length_of_threads += len(aggregatedListThread)
        queue[i] = Queue()
        process[i] = Process(target=run_statistical_tests, kwargs=dict(
            pInteractionFilesList=aggregatedListThread,
            pArgs=args,
            pViewpointObject=viewpointObj,
            pQueue=queue[i]
        )
        )

        process[i].start()
    log.debug('length_of_threads {}'.format(length_of_threads))
    while not all_data_collected:
        for i in range(args.threads):
            if queue[i] is not None and not queue[i].empty():
                background_data_thread = queue[i].get()
                if 'Fail:' in background_data_thread:
                    fail_flag = True
                    fail_message = background_data_thread[6:]
                else:
                    accepted_data[i], rejected_data[i], all_data[i] = background_data_thread
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

    accepted_data = [item for sublist in accepted_data for item in sublist]
    rejected_data = [item for sublist in rejected_data for item in sublist]
    all_data = [item for sublist in all_data for item in sublist]

    writeResultHDF(args.outFileName, accepted_data, rejected_data, all_data, aggregatedList, args.alpha, args.statisticTest)
    # else:
    #     run_statistical_tests(interactionFileList, args)

    # if args.batchMode:
    #     log.debug('rejected_file_names {}'.format(len(rejected_file_names)))
    #     rejected_file_names = [item for sublist in rejected_file_names for item in sublist]
    #     log.debug('rejected_file_names II {}'.format(len(rejected_file_names)))

    #     with open(args.rejectedFileNamesToFile, 'w') as nameListFile:
    #         nameListFile.write('\n'.join(rejected_file_names))
