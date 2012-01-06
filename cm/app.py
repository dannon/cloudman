import config, logging, logging.config, sys
from cm.util import misc
from cm.util import paths

# from cm.clouds.ec2 import EC2Interface
from cm.clouds.eucalyptus import EucaInterface as EC2Interface

log = logging.getLogger( 'cloudman' )
logging.getLogger('boto').setLevel(logging.INFO)

class CMLogHandler(logging.Handler):
    def __init__(self, app):
        logging.Handler.__init__(self)
        self.formatter = logging.Formatter("%(asctime)s - %(message)s", "%H:%M:%S")
        # self.formatter = logging.Formatter("[%(levelname)s] %(module)s:%(lineno)d %(asctime)s: %(message)s")
        self.setFormatter(self.formatter)
        self.logmessages = []

    def emit(self, record):
        self.logmessages.append(self.formatter.format(record))


class UniverseApplication( object ):
    """Encapsulates the state of a Universe application"""
    def __init__( self, **kwargs ):
        print "Python version: ", sys.version_info[:2]
        # Load user data into a local field through a cloud interface
        self.cloud_interface = EC2Interface(app=self)
        self.ud = self.cloud_interface.get_user_data()
        # Setup logging
        self.logger = CMLogHandler(self)
        if self.ud.has_key("testflag"):
            self.TESTFLAG = bool(self.ud['testflag'])
            self.logger.setLevel(logging.DEBUG)
        else:
            self.TESTFLAG = False
            self.logger.setLevel(logging.INFO)
        log.addHandler(self.logger)
        # Read config file and check for errors
        self.config = config.Configuration( **kwargs )
        self.config.check()
        config.configure_logging( self.config )
        log.debug( "Initializing app" )
        self.manager = None
        # Update user data to include persistent data stored in cluster's bucket, if it exists
        # This enables cluster configuration to be recovered on cluster re-instantiation
        if self.ud.has_key('bucket_cluster'):
            if misc.get_file_from_bucket(self.cloud_interface.get_s3_connection(), self.ud['bucket_cluster'], 'persistent_data.yaml', 'pd.yaml'):
                pd = misc.load_yaml_file('pd.yaml')
                self.ud = misc.merge_yaml_objects(self.ud, pd)
        if self.ud.has_key('role'):
            if self.ud['role'] == 'master':
                log.info( "Master starting" )
                from cm.util import master
                self.manager = master.ConsoleManager(self)
            elif self.ud['role'] == 'worker':
                log.info( "Worker starting" )
                from cm.util import worker
                self.manager = worker.ConsoleManager(self)
            self.manager.console_monitor.start()
        else:
            log.error("************ No ROLE in %s - this is a fatal error. ************" % paths.USER_DATA_FILE)
                
    def shutdown(self, delete_cluster=False):
        if self.manager:
            self.manager.shutdown(delete_cluster=delete_cluster)