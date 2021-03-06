#!/usr/bin/python
# This code is part of GET-Evidence.
# Copyright: see COPYING
# Authors: see git-blame(1)

"""
usage: %prog [options]
  --pidfile=PATH: location of pid file
  --stderr=PATH: location of log file
  -h, --host=STRING: the host on which to listen
  -p, --port=NUMBER: the port on which to listen
"""

# Start an XMLRPC server for genome analysis.

import multiprocessing
import re
import os
import subprocess
import sys
import fcntl
import simplejson as json
from optparse import OptionParser
from SimpleXMLRPCServer import SimpleXMLRPCServer
from config_names import GENETESTS_DATA, GETEV_FLAT
from config_names import DBSNP_B36_SORTED, DBSNP_B37_SORTED
from config_names import KNOWNGENE_HG18_SORTED, KNOWNGENE_HG19_SORTED
from config_names import REFERENCE_GENOME_HG18, REFERENCE_GENOME_HG19
from progresstracker import Logger, ProgressTracker
import gff_trio_phase
import get_metadata 
import call_missing 
import gff_twobit_query
import gff_dbsnp_query
import gff_nonsynonymous_filter
import gff_getevidence_map
from conversion import convert_to_gff, detect_format
from utils import autozip

SCRIPT_DIR = os.path.dirname(sys.argv[0])
DEFAULT_BUILD = 'b36'


def process_source(genome_in, metadata=dict(), options=None):
    """
    Open source and return as sorted GFF data.
    """
    # Make best guess of format type, to be saved in metadata.
    metadata['input_type'] = detect_format.detect_format(genome_in)
    print >> sys.stderr, "file format:", metadata['input_type']

    # Open genetic data, decompressing and converting to GFF if necessary.
    gff_input = convert_to_gff.convert(genome_in, options)

    # Grab header (don't sort) & genome build. Pipe the rest to UNIX sort.
    header_done = False
    header = []
    if options and options.getev_only:
        sort_cmd = ['cat']
    elif options and options.sort_buffer_size:
        sort_cmd = ['sort',
                    '--buffer-size=' + options.sort_buffer_size,
                    '--key=1,1', '--key=5n,5', '--key=4n,4']
    else:
        sort_cmd = ['sort', '--key=1,1', '--key=5n,5', '--key=4n,4']
    sort_out = subprocess.Popen(sort_cmd, stdin=subprocess.PIPE, 
                                stdout=subprocess.PIPE, bufsize=1)
    genome_build = DEFAULT_BUILD
    b36_list = ["hg18", "36", "b36", "build36", "NCBI36"]
    b37_list = ["hg19", "37", "b37", "build37", "GRCh37"]

    if options and options.chromosome:
        chromosome_stripped = options.chromosome.lstrip('chr')

    for line in gff_input:
        if not header_done:
            if re.match('#', line):
                header.append(line)
                if line.startswith("##genome-build"):
                    gbdata = line.split()
                    if len(gbdata) < 2:
                        raise Exception("no genome build specified?")
                    elif gbdata[1] in b36_list:
                        genome_build = "b36"
                    elif gbdata[1] in b37_list:
                        genome_build = "b37"
                    else:
                        raise Exception("genome build uninterpretable")
            else:
                header_done = True
        elif (options and options.chromosome and not 
              re.match(r"(?i)^(chr)?%s\s" % chromosome_stripped, line)):
            pass
        else:
            sort_out.stdin.write(str(line.rstrip('\n')) + '\n')
    sort_out.stdin.close()

    # Yield the genome build, followed by the GFF data.
    yield genome_build
    for line in header:
        yield line.rstrip('\n')
    for line in sort_out.stdout:
        yield line.rstrip('\n')

def processing_init(genotype_file, server=None):
    if server:
        server.server_close()
    # Set all the variables we'll use.
    input_dir = os.path.dirname(genotype_file)
    output_dir = input_dir + "-out"
    try:
        os.mkdir (output_dir)
    except OSError:
        pass
    lockfile = os.path.join(output_dir, "lock")
    logfile = os.path.join(output_dir, "log")
    log_handle = open(lockfile, "a+", 0)
    try:
        fcntl.flock(log_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print 'Lockfile really is locked.  Quitting.'
        return None
    log_handle.seek(0)
    log_handle.truncate(0)
    log = Logger(log_handle)
    # Redirect current standard error and output
    if server:
        os.close(sys.stderr.fileno())
        os.close(sys.stdout.fileno())
        os.close(sys.stdin.fileno())
        os.dup2(log_handle.fileno(), sys.stderr.fileno())
        os.dup2(log_handle.fileno(), sys.stdout.fileno())
    return [output_dir, log, log_handle, lockfile, logfile]

def genome_analyzer(genotype_file, server=None, options=None):
    """Perform analyses on genotype_file"""
    init_stuff = processing_init(genotype_file, server)
    if init_stuff:
        output_dir, log, log_handle, lockfile, logfile = init_stuff
    else:
        return None

    # Set up arguments used by processing commands and scripts.
    args = { 'genotype_input': str(genotype_file),
             'miss_out': os.path.join(output_dir, 'missing_coding.json'),
             'sorted_out': os.path.join(output_dir, 'source_sorted.gff.gz'),
             'nonsyn_out_tmp': os.path.join(output_dir, 'ns_tmp.gff.gz'),
             'nonsyn_out': os.path.join(output_dir, 'ns.gff.gz'),
             'getev_out': os.path.join(output_dir, 'get-evidence.json'),
             'getev_genes_out': os.path.join(output_dir, 'get-ev_genes.json'),
             'metadata_out': os.path.join(output_dir, 'metadata.json'),
             'genome_stats': os.path.join(os.getenv('DATA'), 'genome_stats.txt'),
             'genetests': os.path.join(os.getenv('DATA'), GENETESTS_DATA),
             'getev_flat': os.path.join(os.getenv('DATA'), GETEV_FLAT) 
             }

    # Make output directory if needed
    try:
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
    except:
        print "Unexpected error:", sys.exc_info()[0]

    # Read metadata with uploaded file, if available.
    try:
        f_metadata = autozip.file_open(os.path.dirname(genotype_file) + 
                                       '/metadata.json')
        metadata_line = f_metadata.next()
        genome_data = json.loads(metadata_line)
    except IOError:
        genome_data = dict()

    # Process and sort input genome data
    log.put ('#status 0/100 converting and sorting input file')
    gff_in_gen = None
    # Look for parents and, if possible, use these to phase genome.
    if ('parent A' in genome_data and 'parent B' in genome_data):
        parA_in_dir = os.path.join(
            os.path.dirname(os.path.dirname(args['genotype_input'])),
            genome_data['parent A'])
        parB_in_dir = os.path.join(
            os.path.dirname(os.path.dirname(args['genotype_input'])),
            genome_data['parent B'])
        if os.path.exists(parA_in_dir) and os.path.exists(parB_in_dir):
            parA_files = os.listdir(parA_in_dir)
            parA_file_match = [x for x in parA_files if re.match('genotype', x)]
            parB_files = os.listdir(parB_in_dir)
            parB_file_match = [x for x in parB_files if re.match('genotype', x)]
            if parA_file_match and parB_file_match:
                parA_input = os.path.join(parA_in_dir, parA_file_match[0])
                parB_input = os.path.join(parB_in_dir, parB_file_match[0])
                gff_parA_gen = process_source(parA_input, dict(), options=options)
                gff_parB_gen = process_source(parB_input, dict(), options=options)
                gff_child_gen = process_source(args['genotype_input'], 
                                               genome_data, options=options)
                parA_build = gff_parA_gen.next()
                parB_build = gff_parB_gen.next()
                genome_data['genome_build'] = gff_child_gen.next()
                if (parA_build == genome_data['genome_build'] and 
                    parB_build == genome_data['genome_build']):
                    trio_phase = gff_trio_phase.PhaseTrio(gff_child_gen, 
                                                          gff_parA_gen, 
                                                          gff_parB_gen, False)
                    gff_in_gen = trio_phase.call_phase()
    # Set up if trio phasing couldn't be done.
    if not gff_in_gen:
        # We pass build as a yield (instead of in metadata) to force the 
        # generator to read through the header portion of the input data.
        gff_in_gen = process_source(args['genotype_input'], genome_data, options=options)
        genome_data['genome_build'] = gff_in_gen.next()

    # Set up build-dependent file locations
    if (genome_data['genome_build'] == "b36"):
        args['dbsnp'] = os.path.join(os.getenv('DATA'), DBSNP_B36_SORTED)
        args['reference'] = os.path.join(os.getenv('DATA'), 
                                         REFERENCE_GENOME_HG18)
        args['transcripts'] = os.path.join(os.getenv('DATA'), 
                                           KNOWNGENE_HG18_SORTED)
    elif (genome_data['genome_build'] == "b37"):
        args['dbsnp'] = os.path.join(os.getenv('DATA'), DBSNP_B37_SORTED)
        args['reference'] = os.path.join(os.getenv('DATA'), 
                                         REFERENCE_GENOME_HG19)
        args['transcripts'] = os.path.join(os.getenv('DATA'), 
                                           KNOWNGENE_HG19_SORTED)
    else:
        raise Exception("genome build data is invalid")

    if options and options.chromosome:
        chrlist = [options.chromosome]
    else:
        # It might be more elegant to extract this from metadata.
        chrlist = ['chr' + str(x) for x in range(1, 22) + ['X', 'Y']]

    # Process genome through a series of GFF-formatted string generators.
    log.put('#status 20 looking up reference alleles and '
            'dbSNP IDs, computing nonsynonymous changes, '
            'cross-referencing GET-Evidence database')
    progtrack = ProgressTracker(sys.stderr, [22, 99], expected=chrlist, 
                                metadata=genome_data)

    if not options or not options.no_metadata:

        # Record chromosomes seen and genome coverage.
        gff_in_gen = get_metadata.genome_metadata(gff_in_gen,
                                                  args['genome_stats'],
                                                  progresstracker=progtrack)

        # Report coding regions that lack coverage.
        gff_in_gen = call_missing.report_uncovered(gff_in_gen,
                                                   args['transcripts'], 
                                                   args['genetests'], 
                                                   output_file=args['miss_out'],
                                                   progresstracker=progtrack)

    if options and options.metadata_only:
        for line in gff_in_gen:
            pass

    else:
        # Find reference allele.
        gff_in_gen = gff_twobit_query.match2ref(gff_in_gen, args['reference'])

        # Look up dbSNP IDs
        gff_in_gen = gff_dbsnp_query.match2dbSNP(gff_in_gen, args['dbsnp'])

        # Check for nonsynonymous SNP
        gff_in_gen = gff_nonsynonymous_filter.predict_nonsynonymous(gff_in_gen, 
                                                                    args['reference'], 
                                                                    args['transcripts'])

        # Pull off GET-Evidence hits
        gff_in_gen = gff_getevidence_map.match_getev(gff_in_gen,
                                                     args['getev_flat'], 
                                                     transcripts_file=args['transcripts'],
                                                     gene_out_file=args['getev_genes_out'] + ".tmp",
                                                     output_file=args['getev_out'] + ".tmp", 
                                                     progresstracker=progtrack)

        # Printing to output, pulls data through the generator chain.
        ns_out = autozip.file_open(args['nonsyn_out_tmp'], 'w')
        for line in gff_in_gen:
            ns_out.write(line + "\n")
        ns_out.close()

        os.system("mv " + args['getev_out'] + ".tmp " + args['getev_out'])
        os.system("mv " + args['nonsyn_out_tmp'] + " " + args['nonsyn_out'])
        os.system("mv " + args['getev_genes_out'] + ".tmp " + args['getev_genes_out'])

    # Print metadata
    metadata_f_out = open(args['metadata_out'], 'w')
    progtrack.write_metadata(metadata_f_out)
    metadata_f_out.close()

    log.put ('#status 100 finished')

    os.rename(lockfile, logfile)
    log_handle.close()
    print "Finished processing file " + str(genotype_file)


def getev_reprocess(genotype_file, server=None, options=None):
    """Redo analysis against GET-Evidence data"""
    init_stuff = processing_init(genotype_file, server)
    if init_stuff:
        output_dir, log, log_handle, lockfile, logfile = init_stuff
    else:
        return None
    log.put('#status 0 Reprocessing data against GET-Evidence')
    args = { 'metadata': os.path.join(output_dir, 'metadata.json'), 
             'nonsyn_data': os.path.join(output_dir, 'ns.gff'),
             'getev_out': os.path.join(output_dir, 'get-evidence.json'),
             'getev_genes_out': os.path.join(output_dir, 'get-ev_genes.json'),
             'getev_flat': os.path.join(os.getenv('DATA'), GETEV_FLAT)
             }
    # Read metadata file (need this to get build info for transcripts file)
    try:
        f_metadata = autozip.file_open(args['metadata'])
        metadata = json.loads(f_metadata.next())
        f_metadata.close()
        if metadata['genome_build'] == 'b36':
            args['transcripts'] = os.path.join(os.getenv('DATA'),
                                               KNOWNGENE_HG18_SORTED)
        elif metadata['genome_build'] == 'b37':
            args['transcripts'] = os.path.join(os.getenv('DATA'),
                                               KNOWNGENE_HG19_SORTED)
        else:
            raise KeyError
    except (IOError, KeyError):
        fcntl.flock(log_handle, fcntl.LOCK_UN)
        log_handle.close()
        genome_analyzer(genotype_file)
        return
    
    if (os.path.exists (args['nonsyn_data'] + '.gz')):
        args['nonsyn_data'] = args['nonsyn_data'] + '.gz'

    if options and options.chromosome:
        chrlist = [options.chromosome]
    else:
        chrlist = ['chr' + str(x) for x in range(1, 22) + ['X', 'Y']]
    progtrack = ProgressTracker(log_handle, [1, 99], expected=chrlist)

    # Get GET-Evidence hits
    gff_getevidence_map.match_getev_to_file(args['nonsyn_data'],
                                            args['getev_flat'],
                                            transcripts_file=args['transcripts'],
                                            output_file=args['getev_out'] + ".tmp",
                                            gene_out_file=args['getev_genes_out'] + ".tmp",
                                            progresstracker=progtrack)
    os.system("mv " + args['getev_out'] + ".tmp " + args['getev_out'])
    os.system("mv " + args['getev_genes_out'] + ".tmp " + args['getev_genes_out'])
    os.rename(lockfile, logfile)
    log_handle.close()
    print "Finished reprocessing GET-Evidence hits for " + str(genotype_file)


def main():
    """Genome analysis XMLRPC server, or submit analysis on command line"""
    # Parse options.
    usage = ("To run as XMLRPC server:\n%prog [--pidfile=PID_PATH " +
             "--stderr=STDERR_PATH --host=HOST_STRING --port=PORT_NUM\n"
             "To run on command line:\n%prog -g GENOME_DATA")
    parser = OptionParser(usage=usage)
    parser.add_option("-s", "--server", action="store_true", dest="is_server",
                      default=False, help="run as XML-RPC server")
    parser.add_option("--pidfile", dest="pidfile",
                      help="store PID in PID_FILE",
                      metavar="PID_FILE")
    parser.add_option("--stderr", dest="stderr",
                      help="write progress to LOG_FILE",
                      metavar="LOG_FILE")
    parser.add_option("--host", dest="host",
                      help="HOST on which to listen",
                      metavar="HOST")
    parser.add_option("-p", "--port", dest="port",
                      help="PORT on which to listen",
                      metavar="PORT")
    parser.add_option("-g", "--genome", dest="genome_data",
                      help="GENOME_DATA to process",
                      metavar="GENOME_DATA")
    parser.add_option("-C", "--chromosome", dest="chromosome",
                      help="single chromosome to process",
                      metavar="CHROMOSOME")
    parser.add_option("-M", "--metadata-only", dest="metadata_only", action="store_true",
                      help="do not call nsSNPs, just produce statistics",
                      metavar="METADATA_ONLY")
    parser.add_option("--no-metadata", dest="no_metadata", action="store_true",
                      help="do not produce statistics",
                      metavar="NO_METADATA")
    parser.add_option("--sort-buffer-size", dest="sort_buffer_size",
                      help="control --buffer-size option to sort(1)",
                      metavar="SORT_BUFFER_SIZE")
    parser.add_option("--getevonly", action="store_true", dest="getev_only", 
                      default=False, help="Reprocess against GET-Evidence only")
    parser.set_defaults(sort_buffer_size="20%")
    option, args = parser.parse_args()

    if option.genome_data and not option.is_server:
        if option.getev_only:
            getev_reprocess(option.genome_data, options=option)
        else:
            genome_analyzer(option.genome_data, options=option)
    elif option.is_server:
        if option.stderr:
            errout = open(option.stderr, 'a+', 0)
            os.dup2 (errout.fileno(), sys.stdout.fileno())
            os.dup2 (errout.fileno(), sys.stderr.fileno())

        if option.pidfile:
            file(option.pidfile, 'w+').write("%d\n" % os.getpid())

        # figure out the host and port
        host = option.host or "localhost"
        port = int(option.port or 8080)
        
        # create server
        server = SimpleXMLRPCServer((host, port))
        server.register_introspection_functions()
        
        def submit_local(genotype_file):
            """Start subprocess to perform genome analysis"""
            process = multiprocessing.Process(target=genome_analyzer, 
                                        args=(genotype_file,server,))
            process.start()
            print("Job submitted for genotype_file: \'" + 
                  str(genotype_file) + "\', process ID: \'" + 
                  str(process.pid) + "\'")
            return str(process.pid)

        def reprocess_getev(genotype_file):
            """Start subprocess to reprocess against GET-Evidence"""
            process = multiprocessing.Process(target=getev_reprocess, 
                                              args=(genotype_file, server,))
            process.start()
            print("Job submitted for genotype_file: \'" +
                  str(genotype_file) + "\', process ID: \'" +
                  str(process.pid) + "\'")
            return str(process.pid)

        server.register_function(submit_local)
        server.register_function(reprocess_getev)

        # run the server's main loop
        try:
            server.serve_forever()
        except:
            server.server_close()
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
