
import sys
import os
import getpass
import psycopg2
import database
import db_tasks
import migrations
from common import MIGRATIONS_TABLE, BadCommandArguments, FAILURE, SUCCESS, DBMAKE_CONFIG_DIR, \
    DBMAKE_CONFIG_FILE, ZERO_MIGRATION_FILE_NAME, ZERO_MIGRATION_NAME


class BaseCommand:
    """
    A base class for all application's command line commands
    """

    def __init__(self, args=[]):
        """
        :param args: a list of applications command line arguments
        """
        self._parse_options(args)

    def print_help(self):
        raise NotImplementedError

    def execute(self):
        """
        Execute command
        """
        raise NotImplementedError

    def _parse_options(self, args=[]):
        """
        Parses all command options from command line arguments
        """
        raise NotImplementedError


class Init(BaseCommand):

    db_host = None
    db_user = None
    db_port = None
    db_pass = None
    db_name = None
    connection_name = None
    db_connection_config = None
    migrations_dir = None
    drop_existing = False

    def __init__(self, args=[]):
        BaseCommand.__init__(self, args)

    def execute(self):
        if self.migrations_dir is None:
            self.migrations_dir = os.path.abspath(os.getcwd())

        if self.db_pass is None:
            self.db_pass = getpass.getpass("Enter database password: ")

        self.db_connection_config = database.DbConnectionConfig(
            self.db_host,
            self.db_name,
            self.db_user,
            self.db_pass,
            self.connection_name
        )

        # Check connection parameters by establishing a connection to db
        try:
            db_adapter = database.DbAdapterFactory.create(self.db_connection_config)
        except psycopg2.OperationalError as e:
            print "Failed to connect to a database server with specified parameters."
            print e.message.decode()
            return FAILURE

        # Check if dbmake migrations table is already exists in a database
        try:
            schema_table_list = db_adapter.get_tables()
        except psycopg2.Error as e:
            print e.message.decode()
            return FAILURE

        if MIGRATIONS_TABLE in schema_table_list:
            print 'Error! "%s" table is already exists in database.' % MIGRATIONS_TABLE
            return FAILURE

        # Check that migrations directory has no ZERO-MIGRATION file while database is not empty
        zero_migration_file = self.migrations_dir + os.sep + ZERO_MIGRATION_FILE_NAME
        if os.path.exists(zero_migration_file) and len(schema_table_list) > 0:
            print "Error! Database is not empty while ZERO-MIGRATION file already exists."
            return FAILURE

        # TODO: Check that migrations directory has no migration scripts while there's yet no ZERO-MIGRATION file

        # Now let's check if Zero-Migration exists and if true, check whether database is empty
        # If Zero-Migration doesn't exist, generate it out of database's current structure,
        # If it does exist, check whether the database is empty, if it is not then return FAILURE

        remove_zero_migration_file_on_failure = False

        # Get database tasks factory
        db_tasks_factory = db_tasks.AbstractDbTasksFactory.create(database.DbType.POSTGRES)

        if not os.path.exists(zero_migration_file):
            dump_zero_migration_task = db_tasks_factory.create(
                db_tasks.DbTaskType.DUMP_ZERO_MIGRATION,
                self.db_connection_config,
                db_adapter
            )
            result = dump_zero_migration_task.execute(zero_migration_file)

            if not result:
                print "Failed to dump database schema into ZERO-MIGRATION file."
                return FAILURE

            remove_zero_migration_file_on_failure = True

        # Initialize dbmake migrations table
        init_task = db_tasks_factory.create(db_tasks.DbTaskType.INIT, self.db_connection_config, db_adapter)
        result = init_task.execute()

        if not result:
            if remove_zero_migration_file_on_failure:
                os.remove(zero_migration_file)
            return FAILURE

        # Create .dbmake directory if it doesn't exist
        result = self._create_config_dir()

        if result is False:
            if remove_zero_migration_file_on_failure:
                os.remove(zero_migration_file)
            return FAILURE

        # Save connection configuration (also creates config file if it doesn't exist)
        dbmake_config_file = self.migrations_dir + os.sep + DBMAKE_CONFIG_DIR + os.sep + DBMAKE_CONFIG_FILE
        result = self.db_connection_config.save(dbmake_config_file)

        if result is False:
            if remove_zero_migration_file_on_failure:
                os.remove(zero_migration_file)
            return FAILURE

        # Insert migration revision 0 if ZERO-MIGRATION has been created
        # during the database's init
        if remove_zero_migration_file_on_failure:
            # Create new migration record
            migration_vo = migrations.MigrationVO()
            migration_vo.revision = 0
            migration_vo.migration_name = ZERO_MIGRATION_NAME

            # Save the new migration record
            migration_dao = migrations.MigrationsDao(db_adapter)
            migration_dao.create(migration_vo)
        else:
            # Apply MIGRATION-ZERO on the newly initialized database
            # migrations_manager = migrations.MigrationsManager(
            #    self.migrations_dir,
            #    db_adapter
            # )
            migrations_manager = migrations.MigrationsManager(self.migrations_dir)
            result = migrations_manager.migrate_to_revision(0)

            if result is False:
                print "Error! Failed to apply MIGRATION-ZERO."
                return FAILURE

        return SUCCESS

    def _create_config_dir(self):
        """
        Creates a dbmake config directory within migrations folder if it yet doesn't exist.
        :return: Returns True on success, otherwise returns False
        """

        print "Check if .dbmake directory does not exist in: %s ..." % self.migrations_dir,

        dbmake_config_dir = str(self.migrations_dir) + os.sep + DBMAKE_CONFIG_DIR
        if not os.path.exists(dbmake_config_dir):
            print "Not exists"
            print "Create .dbmake directory... ",

            try:
                os.makedirs(dbmake_config_dir)
            except OSError as e:
                msg = e.message()
                print "Failure"
                print msg
                return False
            else:
                print "OK"
        else:
            print "Exists"

        return True

    @staticmethod
    def print_help():
        # --no-dump               Don't dump database structure into ZERO MIGRATION file
        print """
        usage: dbmake init [(-m | --migrations-dir) <path>] <connection name> (options) [OPTIONAL]

        Note:
        <connection name> is used to refer to database connection parameters.

        Optional:
            -m, --migrations-dir    Where migrations reside
            -p, --port              Database server port
            -P, --password          Database username password

        Required options:
            -h, --host      Database server host
            -d, --dbname    Database name
            -u, --user      Database username
        """

    def _parse_options(self, args):
        if len(args) == 0:
            raise BadCommandArguments

        # Parse optional [(-m | --migrations-dir) <path>]
        if args[0] == '-m' or args[0] == '--migrations-dir':
            if len(args) < 2:
                raise BadCommandArguments
            args.pop(0)
            self.migrations_dir = str(args.pop(0))

        elif args[0].startswith("--migrations-dir="):
            self.migrations_dir = str(args[0].split('=')[1])
            args.pop(0)

        # Parse required <connection name>
        if len(args) == 0:
            raise BadCommandArguments
        else:
            self.connection_name = args.pop(0)

        # Parse all the remaining necessary options
        while len(args) > 0:

            # Parse (-h, --host) option
            if (args[0] == '-h' or args[0] == '--host') and len(args) >= 2:
                args.pop(0)
                self.db_host = args.pop(0)

            elif args[0].startswith('--host='):
                self.db_host = args[0].split('=')[1]
                args.pop(0)

            # Parse (-u, --user) option
            elif (args[0] == '-u' or args[0] == '--user') and len(args) >= 2:
                args.pop(0)
                self.db_user = args.pop(0)

            elif args[0].startswith('--user='):
                self.db_user = args[0].split('=')[1]
                args.pop(0)

            # Parse (-d, --dbname) option
            elif (args[0] == '-d' or args[0] == '--dbname') and len(args) >= 2:
                args.pop(0)
                self.db_name = args.pop(0)

            elif args[0].startswith('--dbname='):
                self.db_name = args[0].split('=')[1]
                args.pop(0)

            # Parse [-p, --port] option
            elif (args[0] == '-p' or args[0] == '--port') and len(args) >= 2:
                args.pop(0)
                self.db_port = args.pop(0)

            elif args[0].startswith('--port='):
                self.db_port = args[0].split('=')[1]
                args.pop(0)

            # Parse [-P, --password] option
            elif (args[0] == '-P' or args[0] == '--password') and len(args) >= 2:
                args.pop(0)
                self.db_pass = args.pop(0)

            elif args[0].startswith('--password='):
                self.db_pass = args[0].split('=')[1]
                args.pop(0)

            # Prevent infinite loop caused by wrong arguments
            else:
                args.pop(0)

        print self.__repr__()

        # Create DbConnectionConfig from parsed arguments
        if (
            self.db_host is None
            or self.db_name is None
            or self.db_user is None
            or self.connection_name is None
        ):
            raise BadCommandArguments

    def __repr__(self):
        return "dbhost=%s, dbuser=%s dbname=%s dbpass=%s, conn_name=%s" % (
            self.db_host, self.db_user, self.db_name, self.db_pass, self.connection_name
        )


class Migrate(BaseCommand):

    _MIGRATE_UP = "up"
    _MIGRATE_DOWN = "down"
    connection_name = None
    migrations_dir = None
    target_revision = None
    dry_run = False
    migration_direction = _MIGRATE_UP
    migration_steps = None

    def __init__(self, args=[]):
        BaseCommand.__init__(self, args)

    def execute(self):

        if self.migrations_dir is None:
            self.migrations_dir = os.path.abspath(os.getcwd())

        # Get database connection\s configurations
        connections_configs = []
        if self.connection_name is not None:
            config_file = self.migrations_dir + os.sep + DBMAKE_CONFIG_DIR + os.sep + DBMAKE_CONFIG_FILE
            connections_configs.append(database.DbConnectionConfig.read(config_file, self.connection_name))
        else:
            config_file = self.migrations_dir + os.sep + DBMAKE_CONFIG_DIR + os.sep + DBMAKE_CONFIG_FILE
            connections_configs = database.DbConnectionConfig.read_all(config_file)

        if connections_configs is False or connections_configs[0] is False:
            print "Failed to read config file"
            return FAILURE

        migrations_manager = migrations.MigrationsManager(self.migrations_dir)
        revisions = migrations_manager.revisions()

        for db_connection_config in connections_configs:

            # If migration direction is UP and a schema has no migration yet
            # then the ZERO-MIGRATION must be applied first before applying
            # following migrations
            apply_zero_migration = False

            try:
                db_adapter = database.DbAdapterFactory.create(db_connection_config)
            except psycopg2.OperationalError as e:
                print "%s: Failed to connect database %s on host %s:%s, user: %s" % (
                    db_connection_config.connection_name,
                    db_connection_config.db_name,
                    db_connection_config.host,
                    db_connection_config.port,
                    db_connection_config.user
                )
                print e.message.decode()
                continue

            migrations_dao = migrations.MigrationsDao(db_adapter)

            if migrations_dao.is_migration_table_exists() is True:

                # Find current migration
                recent_migration_vo = migrations_dao.find_most_recent()
                current_revision = int(recent_migration_vo.revision)

                # Find target revision
                if self.target_revision is None and self.migration_steps is None:
                    target_revision = migrations_manager.latest_revision()

                elif self.target_revision is not None:
                    target_revision = self.target_revision

                    if not migrations_manager.is_revision_exists(target_revision):
                        print "Error! Target revision's migration file %s was not found!" % target_revision
                        return FAILURE

                elif self.migration_steps is not None:

                    if current_revision not in revisions:
                        print "%s: Error! Current revision's migration wasn't found." % db_connection_config.connection_name
                        return FAILURE
                    else:
                        current_index = revisions.index(current_revision)

                        if (
                            self.migration_direction == self._MIGRATE_UP
                            and (current_index + self.migration_steps) >= len(revisions)
                        ):
                            # If number of steps exceed size of revisions list, and migration direction is UP,
                            # then set target revision to the latest one
                            target_revision = revisions[-1]
                        elif (
                            self.migration_direction == self._MIGRATE_DOWN
                            and (current_index - self.migration_steps) < 0
                        ):
                            # If number of steps exceed the zero index of a revisions list, and migration
                            # direction is DOWN, then set target revision to the latest one
                            target_revision = 0
                        elif self.migration_direction == self._MIGRATE_UP:
                            target_revision = revisions[current_index + self.migration_steps]
                        elif self.migration_direction == self._MIGRATE_DOWN:
                            target_revision = revisions[current_index - self.migration_steps]
                        else:
                            print "%s: Error! Can't define target revision" % db_connection_config.connection_name
                            return FAILURE

                # Migrate...
                print "%s: Migrating... (target revision:  %s)" % (
                    db_connection_config.connection_name,
                    target_revision
                )
                migrations_manager.migrate_to_revision(target_revision, db_adapter)
                print "-" * 20
            else:
                print "%s: Error! No migrations table has been found." % db_connection_config.connection_name

            db_adapter.disconnect()

        return SUCCESS

    def _target_revision(self, revisions, current_revision):
        """
        Returns a Target Revision for current connection
        :param revisions:
        :param current_revision:
        :return:
        """
        # TODO: Implement _target_revision()
        pass

    @staticmethod
    def print_help():
        # --no-dump               Don't dump database structure into ZERO MIGRATION file
        print """
        usage: dbmake migrate [options] [(--up | --down) <steps> | (-r | --revision=)<value>]

        Note:
        This command will perform database migration according to passed options.
        By default, if no options are specified, the command will migrate all databases
        in the %s file that resides in %s folder within migrations directory.

        Optional:
            -d, --dry-run                         Dry run (print commands, but do not execute)
            -m <path>, --migrations-dir=<path>    Where migrations reside
            -c <name>, --connection=<name>        Connection name of a database to migrate
            -r <value>, --revision=<value>        Number of revision to migrate to
            --up=<steps>                          Number of revisions to migrate UP
            --down=<steps>                        Number of revisions to migrate DOWN (rollback)
        """ % (DBMAKE_CONFIG_FILE, DBMAKE_CONFIG_DIR)

    def _parse_options(self, args):

        options = ['-m', '--migration-dir', '--migrations-dir=', '-c',
                   '--connection', '--connection=', '-r', '--revision', '--revision=',
                   '--up', '--up=', '--down', '--down=', '-d', '--dry-run']

        while len(args) > 0:
            # Parse optional [(-m | --migrations-dir) <path>]
            if args[0] == '-m' or args[0] == '--migrations-dir':
                if len(args) < 2:
                    raise BadCommandArguments
                args.pop(0)
                self.migrations_dir = str(args.pop(0))

            elif args[0].startswith("--migrations-dir="):
                self.migrations_dir = str(args[0].split('=')[1])
                args.pop(0)

            # Parse optional [(c | --connection)]
            elif args[0] == '-c' or args[0] == '--connection':
                if len(args) < 2:
                    raise BadCommandArguments
                args.pop(0)
                self.connection_name = str(args.pop(0))

            elif args[0].startswith("--connection="):
                self.connection_name = str(args[0].split('=')[1])
                args.pop(0)

            # Parse optional [(r | --revision)]
            elif args[0] == '-r' or args[0] == '--revision':
                if len(args) < 2:
                    raise BadCommandArguments
                args.pop(0)
                self.target_revision = abs(int(args.pop(0)))

            elif args[0].startswith("--revision="):
                self.target_revision = abs(int(args[0].split('=')[1]))
                args.pop(0)

            # Parse optional [--up=<steps>]
            elif args[0] == '--up':
                if len(args) < 2:
                    raise BadCommandArguments
                args.pop(0)
                self.migration_direction = self._MIGRATE_UP
                self.migration_steps = abs(int(args.pop(0)))

            elif args[0].startswith("--up="):
                self.migration_direction = self._MIGRATE_UP
                self.migration_steps = abs(int(args[0].split('=')[1]))
                args.pop(0)

            # Parse optional [--down=<steps>]
            elif args[0] == '--down':
                if len(args) < 2:
                    raise BadCommandArguments
                args.pop(0)
                self.migration_direction = self._MIGRATE_DOWN
                self.migration_steps = abs(int(args.pop(0)))

            elif args[0].startswith("--down="):
                self.migration_direction = self._MIGRATE_DOWN
                self.migration_steps = abs(int(args[0].split('=')[1]))
                args.pop(0)

            # Parse optional [(-d | --dry-run)]
            elif args[0] == '-d' or args[0] == '--dry-run':
                args.pop(0)
                self.dry_run = True

            elif args[0] not in options:
                raise BadCommandArguments

        # Parse all the remaining necessary options
        if len(args) > 0:
            raise BadCommandArguments

        print self.__repr__()

    def __repr__(self):
        return "conn_name=%s, migration_direction=%s, migration_steps=%s, target_revision=%s" % (
            self.connection_name, self.migration_direction, self.migration_steps, self.target_revision
        )


class Status(BaseCommand):

    connection_name = None
    migrations_dir = None

    def __init__(self, args=[]):
        BaseCommand.__init__(self, args)

    def execute(self):

        if self.migrations_dir is None:
            self.migrations_dir = os.path.abspath(os.getcwd())

        # Get database connection\s configurations
        connections_configs = []
        if self.connection_name is not None:
            config_file = self.migrations_dir + os.sep + DBMAKE_CONFIG_DIR + os.sep + DBMAKE_CONFIG_FILE
            connections_configs.append(database.DbConnectionConfig.read(config_file, self.connection_name))
        else:
            config_file = self.migrations_dir + os.sep + DBMAKE_CONFIG_DIR + os.sep + DBMAKE_CONFIG_FILE
            connections_configs = database.DbConnectionConfig.read_all(config_file)

        if connections_configs is False or connections_configs[0] is False:
            print "Failed to read config file"
            return FAILURE

        for db_connection_config in connections_configs:
            try:
                db_adapter = database.DbAdapterFactory.create(db_connection_config)
            except psycopg2.OperationalError as e:
                print "%s: Failed to connect database %s on host %s:%s, user: %s" % (
                    db_connection_config.connection_name,
                    db_connection_config.db_name,
                    db_connection_config.host,
                    db_connection_config.port,
                    db_connection_config.user
                )
                print e.message.decode()
                return FAILURE

            migrations_dao = migrations.MigrationsDao(db_adapter)

            if migrations_dao.is_migration_table_exists() is True:
                last_migration = migrations_dao.find_most_recent()

                if last_migration is None:
                    print "%s: No migrations" % db_connection_config.connection_name
                else:
                    print "%s: Revision %s" % (
                        db_connection_config.connection_name,
                        last_migration.revision
                    )
            else:
                print "%s: Error! No migrations table were found." % db_connection_config.connection_name

            db_adapter.disconnect()

        return SUCCESS

    @staticmethod
    def print_help():
        print """
        usage: dbmake status [options]

        Note: <connection name> is used to refer to database connection parameters.

        Options:
            -m, --migrations-dir    Where migrations are reside
            -c, --connection        Connection name to check status with
        """

    def _parse_options(self, args):

        while len(args) > 0:
            # Parse optional [(-m | --migrations-dir) <path>]
            if args[0] == '-m' or args[0] == '--migrations-dir':
                if len(args) < 2:
                    raise BadCommandArguments
                args.pop(0)
                self.migrations_dir = str(args.pop(0))

            elif args[0].startswith("--migrations-dir="):
                self.migrations_dir = str(args[0].split('=')[1])
                args.pop(0)

            # Parse optional [(c | --connection)]
            elif args[0] == '-c' or args[0] == '--connection':
                if len(args) < 2:
                    raise BadCommandArguments
                args.pop(0)
                self.connection_name = str(args.pop(0))

            elif args[0].startswith("--connection="):
                self.connection_name = str(args[0].split('=')[1])
                args.pop(0)

            else:
                args.pop(0)

        # Parse all the remaining necessary options
        if len(args) > 0:
            raise BadCommandArguments

        print self.__repr__()

    def __repr__(self):
        return "(conn_name=%s)" % self.connection_name


class Forget(BaseCommand):

    connection_name = None
    migrations_dir = None

    def __init__(self, args=[]):
        BaseCommand.__init__(self, args)

    def execute(self):

        if self.migrations_dir is None:
            self.migrations_dir = os.path.abspath(os.getcwd())

        # Get database connection configuration
        if self.connection_name is None:
            print "Error! Please provide a name of connection"
            return FAILURE

        config_file = self.migrations_dir + os.sep + DBMAKE_CONFIG_DIR + os.sep + DBMAKE_CONFIG_FILE

        try:
            db_connection_config = database.DbConnectionConfig.read(config_file, self.connection_name)
        except IOError:
            print "Error! Failed to read a config file."
            return FAILURE

        if db_connection_config is False:
            print "Error! A connection with such a name doesn't exist."
            return FAILURE

        try:
            db_adapter = database.DbAdapterFactory.create(db_connection_config)
        except psycopg2.OperationalError as e:
            print "%s: Failed to connect database %s on host %s:%s, user: %s"% (
                db_connection_config.connection_name,
                db_connection_config.db_name,
                db_connection_config.host,
                db_connection_config.port,
                db_connection_config.user
            )
            print e.message.decode()
            return FAILURE

        migrations_dao = migrations.MigrationsDao(db_adapter)

        if migrations_dao.is_migration_table_exists() is True:
            migrations_dao.drop_migrations_table()

        db_adapter.disconnect()

        database.DbConnectionConfig.delete(config_file, self.connection_name)

        return SUCCESS

    @staticmethod
    def print_help():
        print """
        usage: dbmake forget [(-m | --migrations-dir) <path>] <connection name>

        Drops migrations table in database associated with <connection name> and removes
        connection details from dbmake connections config file

        Options:
            -m, --migrations-dir    Where migrations are reside
        """

    def _parse_options(self, args):

        if len(args) == 0:
            raise BadCommandArguments

        # Parse optional [(-m | --migrations-dir) <path>]
        if args[0] == '-m' or args[0] == '--migrations-dir':
            if len(args) < 2:
                raise BadCommandArguments
            args.pop(0)
            self.migrations_dir = str(args.pop(0))

        elif args[0].startswith("--migrations-dir="):
            self.migrations_dir = str(args[0].split('=')[1])
            args.pop(0)

        # Parse <connection name>
        self.connection_name = args.pop(0)

        # Parse all the remaining necessary options
        if len(args) > 0:
            raise BadCommandArguments

        print self.__repr__()

    def __repr__(self):
        return "(conn_name=%s)" % self.connection_name