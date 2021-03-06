import pathlib
import os, getpass, shutil, re, psutil
import pandas
import jinja2
import sh
import logging
import filecmp
import datetime
import numpy
import itertools
import subprocess
import json
from Bio import SeqIO, Phylo
from packaging import version
from bohra.bohra_logger import logger
# from bohra.utils.write_report import Report


class RunSnpDetection(object):
    '''
    A class for Bohra pipeline object
    '''
    
    def __init__(self, args):
        # get date and time
        self.now = datetime.datetime.today().strftime("%d_%m_%y_%H")
        self.day = datetime.datetime.today().strftime("%d_%m_%y")
        # get the working directory
        self.workdir = pathlib.Path(args.workdir)
        # path to pipeline resources
        self.resources = pathlib.Path(args.resources)
        # path to reference and mask
        self.ref = pathlib.Path(args.reference)
        self.check_rerun()
        # 
        # (args.mask)
        if args.mask:
            rerun_core = self.check_mask(args.mask)
        else:
            self.mask = ''
        # path to input file
        if args.input_file == '':
            self.log_messages('warning', 'Input file can not be empty, please set -i path_to_input to try again')
            raise SystemExit()
        else:
            self.input_file = pathlib.Path(args.input_file)
        # # path to a source file for tracking of reference, jobid and mask
        # self.source_log_path = pathlib.Path(self.workdir, 'source.log')
        # job id
        self.job_id = self._name_exists(args.job_id)
        # other variables
        # min aln 
        self.minaln = args.minaln
        # cluster settings
        self.cluster = args.cluster
        # user
        if self.cluster:
            self.json = args.json
            self.queue = args.queue
            self.check_cluster_reqs()
            self.set_cluster_log()
        
        self.user = getpass.getuser()
        # gubbins TODO add back in later!!
        # if not args.gubbins:
        #     self.gubbins = numpy.nan
        # else:
        #     self.gubbins = args.gubbins
        
        self.gubbins = numpy.nan
        self.mdu = args.mdu
        if isinstance(args.prefillpath, str):
            self.prefillpath = args.prefillpath
        elif self.mdu:
            self.prefillpath = pathlib.Path('home', 'seq', 'MDU', 'QC')
        else:
            self.prefillpath = ''
        self.force = args.force
        self.dryrun = args.dry_run
        self.pipeline = args.pipeline
        self.cpus = args.cpus
        # kraken db settings
        self.kraken_db = args.kraken_db
        self.run_kraken = False
        self.assembler = args.assembler
        self.snippy_version = ''
        self.assembler_dict = {'shovill': 'shovill', 'skesa':'skesa','spades':'spades.py'}
        self.use_singularity = args.use_singularity
        self.singularity_path = args.singularity_path
        self.set_snakemake_jobs()

    def check_queue(self, queue):
        '''
        ensure that if running on a cluster queue is set, otherwise quit cleanly
        '''
        if queue in ['sbatch', 'qsub']:
            return queue
        else:
            logger.warning(f"You are running bohra on a cluster? The queue setting is required, please choose either sbatch or qsub and try again")
            raise SystemExit

    
    def check_cluster_reqs(self):
        '''
        check that the cluster.json and run snakemake files are present for running in a HPC environment
        '''
        if self.json == '' and not self.mdu:
            logger.warning(f"The cluster.json file can not be empty. Please provide a valid file.")
            raise SystemExit
        # check json
        self.json = pathlib.Path(self.json)
        if not self.json.exists():
            logger.warning(f"Please check the paths to {self.json}. You must provide valid paths.")
            raise SystemExit
        # check queue
        self.queue = self.check_queue(self.queue)
        
    def set_snakemake_jobs(self):
        '''
        set the number of jobs to run in parallel based on the number of cpus from args
        '''
        if int(self.cpus) < int(psutil.cpu_count()):
            self.jobs =  self.cpus
        else:
            self.jobs = 1
    
    def force_overwrite(self):
        '''
        will force pipeline to run in an existing folder - removes isolate and source logs
        '''
        logger.info(f"You have selected to force overwrite an existing job.")
        isolatelog = self.workdir / f"isolates.log"
        sourcelog = self.workdir / f"source.log"
        # joblog = self.workdir / f"job.log"
        logger.info(f"Removing history.")
        if isolatelog.exists():
            isolatelog.unlink()
        if sourcelog.exists():
            sourcelog.unlink()
        # if joblog.exists():
        #     joblog.unlink()

        return False

    def check_setup_files(self):
        '''
        check that the working directory, resources directory and the input file exist
        '''
        logger.info(f"Checking that all required input files exist.")
        logger.info(f"Checking that {self.workdir} exists.")
        self.path_exists(self.workdir, v = False) 
        logger.info(f"Checking that {self.resources} exists.")
        self.path_exists(self.resources, v = False)
        logger.info(f"Checking that {self.input_file} exists.")
        self.path_exists(self.input_file)
    
    def check_snippy(self):
        '''
        check for snippy
        '''
        logger.info(f"Checking that snippy is installed and recording version.")
        version_pat = re.compile(r'\bv?(?P<major>[0-9]+)\.(?P<minor>[0-9]+)\.(?P<release>[0-9]+)(?:\.(?P<build>[0-9]+))?\b')
        try:
            snippy = subprocess.run(['snippy', '--version'], stderr=subprocess.PIPE)
            snippy = snippy.stderr.decode().strip()
            self.snippy_version = version_pat.search(snippy)
            logger.info(f"Snippy {snippy} found. Good job!")
            return(version_pat.search(snippy))
        except FileNotFoundError:
            logger.info(f"snippy is not installed.")
            raise SystemExit
    
    def check_snippycore(self):
        '''
        check for snippy-core
        '''
        self.check_installation('snippy-core')

    def check_snpdists(self):
        '''
        check for snp-dists
        '''
        self.check_installation('snp-dists')



    def check_iqtree(self):
        '''
        check iqtree
        '''
        self.check_installation('iqtree')

    def check_installation(self,software):
        '''
        Check that software is installed
        input:
            :software: the name of the software - must be command line name 
        '''

        if shutil.which(software):
            logger.info(f"{software} is installed")
        else:
            logger.warning(f"{software} is not installed, please check dependencies and try again.")
            raise SystemExit


    def check_assembler(self):
        '''
        check version of assembler
        '''
        ret = 0
        
        self.check_installation(self.assembler_dict[self.assembler])


    def check_assemble_accesories(self):
        '''
        check the assembly accessories mlst, kraken, abricate and prokka
        '''
        accessories = ['mlst', 'kraken2', 'abricate', 'prokka']
        
        for a in accessories:
            self.check_installation(a)
    
    def check_roary(self):
        '''
        check roary is installed
        '''

        self.check_installation('roary')

    def check_size_file(self, path):
        '''
        check the size of a file
        '''
        s = path.stat().st_size
        return s

    def check_kraken2_files(self, k2db):
        '''
        ensure that kraken2 DB is not empty
        '''
        if pathlib.Path(k2db).is_dir():
                logger.info(f'Found {k2db}, checking that files are not empty')
                kmerfiles = sorted(pathlib.Path(k2db).glob('*'))
                s = []
                for k in range(len(kmerfiles)):
                    s.append(self.check_size_file(pathlib.Path(k2db) / kmerfiles[k]))
                if 0 not in s:
                    self.run_kraken = True
        

    def check_kraken2DB(self):
        '''
        ensure that DB is present and not emtpy
        '''
        logger.info(f'Searching for kraken2 DB {self.kraken_db}')
        if self.kraken_db != f"{pathlib.Path(os.environ['KRAKEN2_DEFAULT_DB'])}":
            logger.info('You are attempting to use a custom kraken2 DB. This is pretty advanced, good luck!')
            if pathlib.Path(self.kraken_db).exists():
                logger.info(f"{self.kraken_db} has been found.")
                self.check_kraken2_files(k2db = self.kraken_db)
            else:
                logger.warning(f"It seems that your settings for the kraken DB are incorrect. Bohra will check for the presence of a default kraken2 DB.")
        elif "KRAKEN2_DEFAULT_DB" in os.environ:
            k2db = pathlib.Path(os.environ["KRAKEN2_DEFAULT_DB"])
            if self.check_kraken2_files(k2db = self.kraken_db):
                self.kraken_db = f"{k2db}"
        
        if self.run_kraken:
            logger.info(f"Congratulations your kraken database is present")  
        else:
            logger.warning(f"Your kraken DB is not installed in the expected path. Speciation will not be performed. If you would like to perform speciation in future please re-read bohra installation instructions.")
            

    def check_deps(self):
        '''
        check dependencies Snippy, snippy-core, snp-dists, iqtree
        '''
        # TODO check all software tools used and is there a way to check database last update??
        # TODO check assemblers
        logger.info(f"Checking software dependencies")
        if self.pipeline != "a":
            self.check_snippycore()
            self.check_snpdists()
            self.check_kraken2DB()
            self.check_iqtree()
            return(self.check_snippy())
        elif self.pipeline != "s":
            self.check_assembler()
            self.check_assemble_accesories()
            self.check_roary()
            return True


    

    def run_checks(self):
        '''
        Run checks prior to start - checking all software is installed, if this is a rerun and the input files
        '''
        self.check_setup_files()
        
        self.check_deps()
        # check reference
        if self.pipeline != 'a':
            if self.ref == '':
                logger.warning(f"You are trying call SNPs, a reference file is required. Please try again using '-r path to reference'")
                raise SystemExit
            else:
                self.ref = self.link_file(self.ref)
        
    def set_cluster_log(self):
        '''
        save the details of cluster configurations
        '''
        logger.info(f"Recording details of your cluster settings.")
        new_df = pandas.DataFrame({'cluster_json': f"{self.json}",'Date':self.day, 'queue': f"{self.queue}"}, index = [0])
        cluster_log = self.workdir / 'cluster.log'

        if cluster_log.exists():
            cluster_df = pandas.read_csv(cluster_log, '\t')
            cluster_df = cluster_df.append(new_df, sort = True)
        else:
            cluster_df = new_df
        
        cluster_df.to_csv(cluster_log , index=False, sep = '\t')


    def set_source_log(self):
        '''
        set the reference, mask and id for tracking and potential.
        
            
        '''   
        # TODO add in options for using singularity containers
        # path if using containers.
        snippy_v = f'singularity_{self.day}' if self.use_singularity else self.snippy_version
        kraken = self.kraken_db if self.run_kraken else ''
        s = True if self.use_singularity else False
        logger.info(f"Recording your settings for job: {self.job_id}")
        new_df = pandas.DataFrame({'JobID':self.job_id, 'Reference':f"{self.ref}",'Mask':f"{self.mask}", 
                                    'MinAln':self.minaln, 'Pipeline': self.pipeline, 'CPUS': self.cpus, 'Assembler':self.assembler,
                                    'Date':self.day, 'User':self.user, 'snippy_version':snippy_v, 'input_file':f"{self.input_file}",'prefillpath': self.prefillpath, 'cluster': self.cluster,'singularity': s, 'kraken_db':kraken}, 
                                    index=[0], )
        
        source_path = self.workdir / 'source.log'
        if source_path.exists():
            source_df = pandas.read_csv(source_path, '\t')
            source_df = source_df.append(new_df, sort = True)
        else:
            source_df = new_df
        
        source_df.to_csv(source_path , index=False, sep = '\t')

    def check_rerun(self):
        '''
        Check if the job is a rerun of an existing job, if so print message informing user and exit

        '''

        source_path = self.workdir / 'source.log'
        # if the path is a string convert to Path
        if isinstance(source_path, str):
            source_path = pathlib.Path(source_path)
        if source_path.exists():
            logger.warning(f"This may be a re-run of an existing job. Please try again using rerun instead of run OR use -f to force an overwrite of the existing job.")
            logger.warning(f"Exiting....")
            
            raise SystemExit()
        else:
            return False


    def path_exists(self,path, v = True):
        '''
        ensure files are present, if so continues if not quits with FileNotFoundError
        input:
            :path: patht to files for pipeline
            :v: if v == True print message, else just check
        output:
            returns True (or fails with FileNotFoundError)
        '''
        
        if not path.exists():
            logger.warning(f"The {path.name} does not exist.")
            raise FileNotFoundError(f"{path.name}")
        else:
            if v == True:
                logger.info(f"Found {path.name}.")

            return True

    
        


    def _name_exists(self, name):
        '''
        check if the name is an empty string JOB id can not be empty
       
        '''
        
        if isinstance(name, str):
            if len(name) == 0:
                logger.warning('Job id ca not be empty, please set -j job_id to try again')
                raise SystemExit()
            else:
                return name
        else:
            logger.warning('Job id ca not be empty, please set -j job_id to try again')
            raise SystemExit()

    def link_reads(self, read_source, isolate_id, r_pair):
        '''
        check if read source exists if so check if target exists - if not create isolate dir and link. If already exists report that a dupilcation may have occured in input and procedd

        '''
        # check that job directory exists
        # logger.info(f"Checking that reads are present.")
        J = pathlib.Path(self.workdir, self.job_id)
        if not J.exists():
            J.mkdir()
        # check that READS exists
        R = J / 'READS'
        if not R.exists():
            R.mkdir()
        
        if f"{read_source}"[0] != '/':
            read_source = self.workdir / read_source
        
        if read_source.exists():
            I = R / f"{isolate_id}" # the directory where reads will be stored for the isolate
            if not I.exists():
                I.mkdir()
            read_target = I / f"{r_pair}"
            if not read_target.exists():
                read_target.symlink_to(read_source)
        else:
            logger.warning(f"{read_source} does not seem to a valid path. Please check your input and try again.")
            raise SystemExit()

    def unzip_files(self,path, suffix):
        '''
        if a zipped reference is provided try to unzip and then return the unzipped pathname. If unable to unzip then supply message and exit
        input:
            :path: pathname  of file to unzip string
            :unzipped: unzipped path
        '''
        logger.info(f"Checking if reference needs to be unzipped")
        target = self.workdir / path.name.strip(suffix)
        
        if suffix == '.zip':
            cmd = f"unzip {path} -d {target}"
        elif suffix == '.gz':   
            cmd = f"gzip -d -c {path} > {target}"
        else:
            logger.warning(f"{path} can not be unzipped. This may be due to file permissions, please provide path to either an uncompressed reference or a file you have permissions to.")
            raise SystemExit

        try:
            logger.info(f"Trying to unzip reference.")
            subprocess.run(cmd, shell = True)
            return target.name
        except:
            logger.warning(f"{path} can not be unzipped. This may be due to file permissions, please provide path to either an uncompressed reference or a file you have permissions to.")
            raise SystemExit            

        


    def link_file(self, path):
        '''
        check if file exists and copy to workingdir
        input:
            :path: path to file
        if path does not exist then return false (calling function will request path). 
        if it does exist, then create symlink to the working dir 
        output:
            returns path.name (str)   
        '''
        
        logger.info(f"Getting input files.") 
        if path.exists():
            if f"{path.suffix}" in ['.gz','zip']:
                    path = pathlib.Path(self.unzip_files(path, f"{path.suffix}"))
                    if not path.exists():
                        logger.warning(f"{path} does not exist. Please try again.")
                        raise SystemExit
            else:
                target = self.workdir / path.name
                # use rename to copy reference to working directory
                # if the reference is not already in the working directory symlink it to working dir
                if not target.exists():
                    logger.info(f"Linking {path.name} to {self.workdir.name}")
                    target.symlink_to(path)
                    found = True
                    
        else:
            logger.warning(f"Path to {path} does not exist or is not a valid file type (.gbk, .fa, .fasta, .gbk.gz, .fa.gz, .fasta.gz). Please provide a valid path to a file and try again")
            raise SystemExit
            # path = pathlib.Path(path)
        
        return  path.name

    def check_mask(self, mask, original_mask = False):
        '''
        input:
            :mask: path to mask file (str)
            :original_mask: name of orignal mask to use in rerun
        output:
            :mask: path to mask  file in workingdir (str) and a boolean True == rerun snippy-core, tree and distance, False == no need to rerun snippy-core and tree
        '''
        
        # if there is a file path added the generate a symlink
        if len(mask) > 0:
            logger.info(f"Checking that mask file exists.")
            m = pathlib.Path(mask)
            if f"{m.name}" == original_mask:
                self.mask = original_mask
                return original_mask
            else:
                m = self.link_file(m)
                self.mask = m
                return m
        elif len(mask) == 0 and original_mask:
            self.mask = original_mask
            return original_mask
        else:
            return ''
    
    def min_four_samples(self, tab):
        '''
        Ensure that there are a minimum of four samples
        returns True if four or more samples
        '''
        logger.info(f"Checking that there are a minimum of 4 isolates.")
        return tab.shape[0] < 4

    def three_cols(self, tab):
        '''
        Ensure that there are 3 columns, isolate, R1 and R2
        returns True if 3 columns False otherwise
        
        '''
        logger.info(f"Checking that input file is the correct structure.")
        if tab.shape[1] == 3:
            return True
        else:
            return False

    def all_data_filled(self, tab):
        '''
        Ensure that all fields contain data - no NA's
        returns True if there are no nan, False otherwise
        '''
        logger.info("Checking that there is no empty fields in the input file.")
        return tab.isnull().sum().sum() == 0
    

    def check_input_structure(self, tab):
        '''
        check that the structure of the input file is correct, 3 columns, with a minimum of 4 isolates
        :input
            :tab: dataframe of the input file
        :output
            True if make it to the end without exiting with a TypeWarning the user of the reason file was rejected
        '''
        # if the structure of the file is incorrect tell user and kill process
        # not the right information (not three columns)
        
        if not self.three_cols(tab):
            logging.warning(f"{self.input_file} does not appear to be in the correct configuration")
            raise TypeError(f"{self.input_file} has incorrect number of columns")
        # if there are not enough isolates (>4)
        
        if self.min_four_samples(tab):
            logger.warning(f"{self.input_file} does not contain enough isolates. The minimum is 4.")
            raise TypeError(f"{self.input_file} has incorrect number of isolates")
        # if any na present indicates that not the full info has been provided
        if not self.all_data_filled(tab):
            logger.warning('warning',f"{self.input_file} appears to be missing some inforamtion.")
            raise TypeError(f"{self.input_file} appears to be missing some inforamtion.")
        
        return True

    def check_reads_exists(self, tab):
        '''
        check that the correct paths have been given
        if reads not present path_exists will cause a FileNotFound error and warn user
        :input
            :tab: dataframe of the input file
        '''
        logger.info(f"Checking that all the read files exist.")
        for i in tab.itertuples():
            
            if not '#' in i[1]:
                r1 = i[2]
                r2 = i[3]
                self.path_exists(pathlib.Path(r1), v = False)
                self.link_reads(pathlib.Path(r1), isolate_id=f"{i[1].strip()}", r_pair='R1.fq.gz')
                self.path_exists(pathlib.Path(r2), v = False)
                self.link_reads(pathlib.Path(r2), isolate_id=f"{i[1].strip()}", r_pair='R2.fq.gz')
        return True

    def set_isolate_log(self, tab, logfile, validation = False):
        '''
        add the isolates to a log file also adds in anoterh check that this is a rerun of an existing job
        input:
            :tab: dataframe of the isolates to add - same structure as original input file, but not needing to be > 4 isolate
            :logfile: path to logfile
            
        '''        
        self.check_input_structure(tab=tab)
        self.check_reads_exists(tab=tab)
        logger.info(f"Recording the isolates used in job: {self.job_id} on {self.day}")
        lf = pandas.DataFrame({'Isolate': [i for i in list(tab.iloc[ : , 0]) if '#' not in i ], 'Status': f"INCLUDED", 'Date': self.day})
        lf['Status'] = numpy.where(lf['Isolate'].str.contains('#'), f"REMOVED", lf['Status'])
        isolates = lf[lf['Status'].isin(['INCLUDED', 'ADDED'])]['Isolate']
        lf.to_csv(logfile, sep = '\t', index = False)
        return list(isolates)
        
    
    def set_workflow_input(self, validation = False):
        '''
        read the input file and check that it is the correct format
        input:
            
        output:
            a list of isolates that will be used in generation of job configfile.
        '''
        logfile = pathlib.Path(self.workdir, 'isolates.log') 
        # make df of input file
        

        # the path to the user provided input file check if it exists
        # read the file
        
        tab = pandas.read_csv(self.input_file, sep = None, engine = 'python', header = None)
        
        
        isolates = self.set_isolate_log(tab = tab, logfile = logfile, validation = validation)
        
        logger.info(f"This job : {self.job_id} contains {len(list(set(isolates)))}")
        return(list(set(isolates))) 
    
    def kraken_output(self):
        '''
        the all output if running kraken
        '''
        return f"\"species_identification.tab\",\n\"report/species_identification.tab\",\nexpand(\"{{sample}}/kraken.tab\",sample = SAMPLE)"
    
    def kraken_ind_string(self):
        '''
        the kraken rule for combination kraken
        '''
        mem_mapping = "--memory-mapping" if not self.cluster else ''
        return(f"""
rule kraken:
	input:
		'READS/{{sample}}/R1.fq.gz',
		'READS/{{sample}}/R2.fq.gz'
	output:
		"{{sample}}/kraken.tab"
	shell:
		\"""
		KRAKENPATH={self.prefillpath}{{wildcards.sample}}/kraken2.tab
		if [ -f $KRAKENPATH ]; then
			cp $KRAKENPATH {{output}}
		else
			kraken2 --paired {{input[0]}} {{input[1]}} --minimum-base-quality 13 --report {{output}} {mem_mapping}
		fi
		\"""
		
""")

    def kraken_combine_string(self):
        '''
        string for combining kraken
        '''
        return(f"""
rule combine_kraken:
	input: 
		expand(\"{{sample}}/kraken.tab\", sample = SAMPLE)
	output:
		\"species_identification.tab\"
	run:
		import pandas, pathlib, subprocess
		kfiles = f\"{{input}}\".split()
		id_table = pandas.DataFrame()
		for k in kfiles:
			kraken = pathlib.Path(k)
			df = pandas.read_csv(kraken, sep = \"\\t\", header =None, names = ['percentage', 'frag1', 'frag2','code','taxon','name'])
			df['percentage'] = df['percentage'].apply(lambda x:float(x.strip('%')) if isinstance(x, str) == True else float(x)) #remove % from columns
			df = df.sort_values(by = ['percentage'], ascending = False)
			df = df[df['code'].isin(['U','S'])]     
			df = df.reset_index(drop = True) 
			tempdf = pandas.DataFrame()
			d = {{'Isolate': f\"{{kraken.parts[0]}}\",    
					'#1 Match': df.ix[0,'name'].strip(), '%1': df.ix[0,'percentage'],
					'#2 Match': df.ix[1,'name'].strip(), '%2': df.ix[1,'percentage'],       
					'#3 Match': df.ix[2,'name'].strip(), '%3': df.ix[2,'percentage'] ,
					'#4 Match': df.ix[3,'name'].strip(), '%4': df.ix[3,'percentage']
					}}
		
			tempdf = pandas.DataFrame(data = d, index= [0])
			if id_table.empty:
					id_table = tempdf
			else:
					id_table = id_table.append(tempdf, sort = True)
		id_table.to_csv(f\"{{output}}\", sep = \"\\t\", index = False)
		subprocess.run(f"sed -i 's/%[0-9]/%/g' {{output}}", shell=True)
""")
    def species_summary(self):
        return "'species_identification.tab'"

    def kraken_report(self):

        return "'report/species_identification.tab'"

    def kraken_copy(self):

        return "cp species_identification.tab report/species_identification.tab"

    def write_pipeline_job(self, maskstring,  script_path = f"{pathlib.Path(__file__).parent / 'utils'}", resource_path = f"{pathlib.Path(__file__).parent / 'templates'}"):
        '''
        write out the pipeline string for transfer to job specific pipeline
        '''
        
        wd = self.workdir / self.job_id
        
        
        kraken_output = self.kraken_output() if self.run_kraken else ''
        kraken_rule = self.kraken_ind_string() if self.run_kraken else ''
        kraken_summary = self.kraken_combine_string() if self.run_kraken else ''
        kraken_report = self.kraken_report() if self.run_kraken else ''    
        copy_species_id = self.kraken_copy() if self.run_kraken else ''  
        species_summary = self.species_summary() if self.run_kraken else '' 

        pipeline_setup = {
            's':'Snakefile_snippy',
            'sa':'Snakefile_default_',
            'a':'Snakefile_assembly',
            'all': 'Snakefile_all'
        }
        vars_for_file = {
            'workdir': f"{wd}",
            'script_path' : script_path,
            'prefill_path' : self.prefillpath,
            'singularity_dir' : self.singularity_path, 
            'job_id' : self.job_id,
            'assembler' : self.assembler if self.pipeline != 's' else 'no_assembler',
            'run_kraken' : self.run_kraken,
            'maskstring': maskstring, 
            'template_path':resource_path,
            'kraken_output':kraken_output,
            'kraken_rule' : kraken_rule,
            'kraken_summary': kraken_summary,
            'species_report': kraken_report,
            'species_summary':species_summary,
            'copy_species_id': copy_species_id
        }
        
        logger.info(f"Writing Snakefile for job : {self.job_id}")
        snk_template = jinja2.Template(pathlib.Path(self.resources, pipeline_setup[self.pipeline]).read_text())
        snk = self.workdir / 'Snakefile'

        snk.write_text(snk_template.render(vars_for_file)) 
        
        logger.info(f"Snakefile successfully created")
        
        

    def json_setup(self, queue_args):
        '''
        Using the json file provided determine the args to be used in command
        '''
        # print(queue_args)
        logger.info(f"Getting settings from {self.json}")
        try:
            with open(self.json) as f:
                json_file = json.load(f)
            if '__default__' in json_file:
                defs = json_file['__default__']
                arg_list = [i for i in defs]
                arg_cluster = []
                for a in arg_list:
                    if a in queue_args and self.queue == 'sbatch':
                        arg_cluster.append(f"{queue_args[a]} {{cluster.{a}}}")
                    elif a in queue_args and self.queue == 'qsub':
                        string = f"{queue_args[a]} {{cluster.{a}}}" if a not in ['time', 'cpus-per-task', 'mem'] else f"{queue_args[a]}{{cluster.{a}}}"
                        arg_cluster.append(string)
                    else:
                        self.log_messages('warning', f'{a} is not a valid option. Please read docs and try again')
                        raise SystemExit
                return ' '.join(arg_cluster)
        except json.decoder.JSONDecodeError:
            logger.warning(f'There is something wrong with your {self.json} file. Possible reasons for this error are incorrect use of single quotes. Check json format documentation and try again.')


    def cluster_cmd(self):

        queue_args = ""
        logger.info(f"Setting up cluster settings for {self.job_id} using {self.json}")
        if self.queue == 'sbatch':
            queue_args = {'account':'-A' ,'cpus-per-task':'-c',  'time': '--time', 'partition':'--partition', 'mem':'--mem', 'job':'-J'}
            queue_cmd = f'sbatch'
        elif self.queue == 'qsub':
            queue_args = {'account':'-P' ,'cpus-per-task': '-l ncpus=',  'time': '-l walltime=', 'partition':'-q', 'mem':'-l mem=', 'job':'-N'}
            queue_cmd = f'qsub'
        else:
            logging.warning(f'{self.queue} is not supported please select sbatch or qsub. Alternatively contact developer for further advice.')
            raise SystemExit
    
        queue_string = self.json_setup(queue_args = queue_args)

        return f"snakemake -j 999 --cluster-config {self.json} --cluster '{queue_cmd} {queue_string}'"

        


    
    def setup_workflow(self, isolates, config_name = 'config.yaml', snake_name = 'Snakefile'):
        '''
        generate job specific snakefile and config.yaml
        input:
            :isolates: a list of isolates that need to be included
        '''

        logger.info(f"Setting up {self.job_id} specific workflow")
        
        gubbins_string = ""
        # make a masking string

        if self.mask != '':
            maskstring = f"--mask {self.workdir / self.mask}"
        else:
            maskstring = ''
        logger.info(f"Writing config file for job : {self.job_id}")
        # read the config file which is written with jinja2 placeholders (like django template language)
        config_template = jinja2.Template(pathlib.Path(self.resources, 'config_snippy.yaml').read_text())
        config = self.workdir / f"{self.job_id}"/ f"{config_name}"
        
        config.write_text(config_template.render(reference = f"{pathlib.Path(self.workdir, self.ref)}", cpus = self.cpus, name = self.job_id,  minperc = self.minaln,now = self.now, maskstring = maskstring, day = self.day, isolates = ' '.join(isolates)))
        
        logger.info(f"Config file successfully created")

        self.write_pipeline_job(maskstring = maskstring)
        

 
    def run_workflow(self,snake_name = 'Snakefile'):
        '''
        run snp_detection
        set the current directory to working dir for correct running of pipeline
        if the pipeline works, return True else False
        '''
        if self.use_singularity:
            singularity_string = f"--use-singularity --singularity-args '--bind /home'"
        else:
            singularity_string = ''

        if self.force:
            force = f"-F"
        else:
            force = f""
        os.chdir(self.workdir)
        
        if self.dryrun:
            dry = '-np'
        else:
            dry = ''

        if self.cluster:
            cmd = f"{self.cluster_cmd()} -s {snake_name} {force} {singularity_string} --latency-wait 1200"
        else:
            cmd = f"snakemake {dry} -s {snake_name} --cores {self.cpus} {force} {singularity_string} 2>&1 | tee -a bohra.log"
            # cmd = f"snakemake -s {snake_name} --cores {self.cpus} {force} "
        logger.info(f"Running job : {self.job_id} with {cmd} this may take some time. We appreciate your patience.")
        wkf = subprocess.run(cmd, shell = True)
        if wkf.returncode == 0:
            return True
        else:
            return False



    def run_pipeline(self):
        '''
        run pipeline, if workflow runs to completion print out a thank you message.
        '''
        # if -f true force a restart
        if self.force:
            self.force_overwrite()
        # check the pipeline setup 
        if self.use_singularity:
            logger.info(f"You have chosen to run bohra with singularity containers. Good luck")
        else:
            self.run_checks()
        
        # update source data in source.log
        self.set_source_log()
        
        # open the input file and check it is in the minimal correct format 
        isolates = self.set_workflow_input()
        
        # setup the workflow files Snakefile and config file
        self.setup_workflow(isolates = isolates)
        
        # run the workflow
        if self.run_workflow():
            # TODO add in cleanup function to remove snakemkae fluff 
            if not self.dryrun:
                logger.info(f"Report can be found in {self.job_id}")
                logger.info(f"Process specific log files can be found in process directories. Job settings can be found in source.log") 
            else:
                if self.force:
                    force = f"-F"
                else:
                    force = f""
                logger.info(f"snakemake -j {self.jobs} {force} 2>&1 | tee -a bohra.log")
            logger.info(f"Have a nice day. Come back soon.") 
            

