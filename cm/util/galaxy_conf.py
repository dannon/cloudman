from os.path import join, exists
from os import makedirs, symlink, chown
from shutil import copyfile, move

from ConfigParser import SafeConfigParser
from pwd import getpwnam
from grp import getgrnam

from .misc import run
from cm.util import paths

import logging
log = logging.getLogger('cloudman')


OPTIONS_FILE_NAME = 'universe_wsgi.ini'


def attempt_chown_galaxy_if_exists(path):
    """
    Change owner of file at specified `path` (if it exists)
    to `galaxy`.
    """
    if exists(path):
        attempt_chown_galaxy(path)


def attempt_chown_galaxy(path):
    """
    Change owner of file at specified `path` to `galaxy`.
    """
    try:
        galaxy_uid = getpwnam("galaxy")[2]
        galaxy_gid = getgrnam("galaxy")[2]
        chown(path, galaxy_uid, galaxy_gid)
    except BaseException:
        run("chown galaxy:galaxy '%s'" % path)


def populate_admin_users(option_manager, admins_list=[]):
    """ Galaxy admin users can now be added by providing them in user data
        (see below) or by calling this method and providing a user list.
        YAML format for user data for providing admin users
        (note that these users will still have to manually register
        on the given cloud instance):
        admin_users:
         - user@example.com
         - user2@anotherexample.edu """
    for admin in option_manager.app.ud.get('admin_users', []):
        if admin not in admins_list:
                admins_list.append(admin)
    if len(admins_list) == 0:
        return False
    log.info('Adding Galaxy admin users: %s' % admins_list)
    option_manager.set_properties({"admin_users": ",".join(admins_list)})


def populate_dynamic_options(option_manager):
    """
    Use `option_manager` to populate arbitrary app:main and galaxy:tool_runners
    properties coming in from userdata.
    """
    dynamic_option_types = {"galaxy_universe_": "app:main",
                            "galaxy_tool_runner_": "galaxy:tool_runners",
                            }
    for option_prefix, section in dynamic_option_types.iteritems():
        for key, value in option_manager.app.ud.iteritems():
            if key.startswith(option_prefix):
                key = key[len(option_prefix):]
                option_manager.set_properties({key: value}, section=section)


# # High-level functions that utilize option_manager interface (defined below)
# # to configure Galaxy's options.
def populate_process_options(option_manager):
    """
    Use `option_manager` to populate process (handler, manager, web) sections
    for Galaxy.
    """
    app = option_manager.app
    web_thread_count = int(app.ud.get("web_thread_count", 1))
    handler_thread_count = int(app.ud.get("handler_thread_count", 1))
    # Setup web threads
    [__add_server_process(option_manager, i, "web", 8080) \
        for i in range(web_thread_count)]
    # Setup handler threads
    handlers = [__add_server_process(option_manager, i, "handler", 9080) \
        for i in range(handler_thread_count)]
    # Setup manager thread
    __add_server_process(option_manager, 0, "manager", 8079)
    process_properties = {"job_manager": "manager0",
                          "job_handlers": ",".join(handlers)}
    option_manager.set_properties(process_properties)


def __add_server_process(option_manager, index, prefix, initial_port):
    app = option_manager.app
    port = initial_port + index
    threads = app.ud.get("threadpool_workers", "7")
    server_options = {"use": "egg:Paste#http",
                      "port": port,
                      "use_threadpool": True,
                      "threadpool_workers": threads
                      }
    server_name = "%s%d" % (prefix, index)
    if port == 8080:
        # Special case, server on port 8080 must be called main unless we want
        # to start deleting chunks of universe_wsgi.ini.
        server_name = "main"
    option_manager.set_properties(server_options,
                                  section="server:%s" % server_name,
                                  description="server_%s" % server_name)
    return server_name


# # Abstraction for interacting with Galaxy's options
def galaxy_option_manager(app):
    """ Returns a high-level class for managing Galaxy options.
    """
    ud = app.ud
    if "galaxy_conf_dir" in ud:
        option_manager = DirectoryGalaxyOptionManager(app)
    else:
        option_manager = FileGalaxyOptionManager(app)
    return option_manager


def populate_galaxy_paths(option_manager):
    """
    Turn ``path_resolver`` paths and configurations into Galaxy options using
    specified ``option_manager``.
    """
    properties = {}
    path_resolver = option_manager.app.path_resolver
    properties["database_connection"] = "postgres://galaxy@localhost:{0}/galaxy"\
        .format(paths.C_PSQL_PORT)
    properties["genome_data_path"] = \
        join(path_resolver.galaxy_indices, "genomes")
    properties["len_file_path"] = \
        join(path_resolver.galaxy_data, "configuration_data", "len")
    properties["tool_dependency_dir"] = \
        join(path_resolver.galaxy_tools, "tools")
    properties["file_path"] = join(path_resolver.galaxy_data, "files")
    temp_dir = join(path_resolver.galaxy_data, "tmp")
    properties["new_file_path"] = temp_dir
    properties["job_working_directory"] = \
        join(temp_dir, "job_working_directory")
    properties["cluster_files_directory"] = \
        join(temp_dir, "pbs")
    properties["ftp_upload_dir"] = \
        join(temp_dir, "ftp")
    properties["nginx_upload_store"] = \
        join(path_resolver.galaxy_data, "upload_store")
    option_manager.set_properties(properties, description="paths")


class FileGalaxyOptionManager(object):
    """
    Default Galaxy option manager, modifies $galaxy_home/universe_wsgi
    directly.
    """

    def __init__(self, app):
        self.app = app

    def setup(self):
        """ setup should return conf_dir, in this case there is none."""
        return None

    def set_properties(self, properties, section="app:main", description=None):
        galaxy_home = self.app.path_resolver.galaxy_home
        config_file_path = join(galaxy_home, OPTIONS_FILE_NAME)
        parser = SafeConfigParser()
        configfile = open(config_file_path, 'rt')
        parser.readfp(configfile)
        for key, value in properties.iteritems():
            parser.set(section, key, value)
        configfile.close()
        new_config_file_path = join(galaxy_home, 'universe_wsgi.ini.new')
        with open(new_config_file_path, 'wt') as output_file:
                parser.write(output_file)
        move(new_config_file_path, config_file_path)
        attempt_chown_galaxy(config_file_path)


class DirectoryGalaxyOptionManager(object):
    """
    When `galaxy_conf_dir` in specified in UserData this is used to
    manage Galaxy's options.
    """

    def __init__(self, app, conf_dir=None, conf_file_name=OPTIONS_FILE_NAME):
        self.app = app
        if not conf_dir:
            conf_dir = app.ud["galaxy_conf_dir"]
        self.conf_dir = conf_dir
        self.conf_file_name = conf_file_name

    def setup(self):
        """ Setup the configuration directory and return conf_dir. """
        self.__initialize_galaxy_config_dir()
        return self.conf_dir

    def __initialize_galaxy_config_dir(self):
        conf_dir = self.conf_dir
        if not exists(conf_dir):
            makedirs(conf_dir)
            defaults_destination = join(conf_dir, "010_%s" % self.conf_file_name)
            galaxy_home = self.app.path_resolver.galaxy_home
            universe_wsgi = join(galaxy_home, self.conf_file_name)
            if not exists(universe_wsgi):
                # Fresh install, take the oppertunity to just link in defaults
                sample_name = "%s.sample" % self.conf_file_name
                defaults_source = join(galaxy_home, sample_name)
                symlink(defaults_source, defaults_destination)
            else:
                # CloudMan has previously been run without the galaxy_conf_dir
                # option enabled. Users may have made modifications to
                # universe_wsgi.ini that I guess we should preserve for
                # backward compatibility.
                defaults_source = join(galaxy_home, self.conf_file_name)
                copyfile(defaults_source, defaults_destination)

    def set_properties(self, properties, section="app:main", description=None):
        if not properties:
            return

        prefix = self.app.ud.get("galaxy_option_priority", "400")
        conf_dir = self.conf_dir
        if description == None:
            description = properties.keys()[0]
        conf_file_name = "%s_cloudman_override_%s.ini" % (prefix, description)
        conf_file = join(conf_dir, conf_file_name)
        props_str = "\n".join(
            ["%s=%s" % (k, v) for k, v in properties.iteritems()])
        open(conf_file, "w").write("[%s]\n%s" % (section, props_str))