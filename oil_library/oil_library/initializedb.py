import os
import sys

import transaction
from sqlalchemy import engine_from_config

from .oil_library_parse import OilLibraryFile

from .models import DBSession, Base

from .init_imported_record import purge_old_records, add_oil_object
from .init_categories import process_categories
from .init_oil import process_oils


def initialize_sql(settings):
    engine = engine_from_config(settings, 'sqlalchemy.')
    DBSession.configure(bind=engine)
    Base.metadata.create_all(engine)


def load_database(settings):
    with transaction.manager:
        # -- Our loading routine --
        session = DBSession()

        # 1. purge our builtin rows if any exist
        sys.stderr.write('Purging old records in database')
        imported_recs_purged, oil_recs_purged = purge_old_records(session)
        print ('finished!!!\n'
               '    {0} imported records purged.\n'
               '    {0} oil records purged.'
               .format(imported_recs_purged, oil_recs_purged))

        # 2. we need to open our OilLib file
        print 'opening file: %s ...' % (settings['oillib.file'])
        fd = OilLibraryFile(settings['oillib.file'])
        print 'file version:', fd.__version__

        # 3. iterate over our rows
        sys.stderr.write('Adding new records to database')
        rowcount = 0
        for r in fd.readlines():
            if len(r) < 10:
                print 'got record:', r

            # 3a. for each row, we populate the Oil object
            add_oil_object(session, fd.file_columns, r)

            if rowcount % 100 == 0:
                sys.stderr.write('.')

            rowcount += 1

        print 'finished!!!  %d rows processed.' % (rowcount)

        process_oils(session)
        process_categories(session)


def make_db(oillib_file=None, db_file=None):
    '''
    Entry point for console_script installed by setup
    '''
    pck_loc = os.path.split(__file__)[0]

    if not db_file:
        db_file = os.path.join(pck_loc, 'OilLib.db')

    if not oillib_file:
        oillib_file = os.path.join(pck_loc, 'OilLib')

    sqlalchemy_url = 'sqlite:///{0}'.format(db_file)
    settings = {'sqlalchemy.url': sqlalchemy_url,
                'oillib.file': oillib_file}
    try:
        initialize_sql(settings)
        load_database(settings)
    except:
        print "FAILED TO CREATED OIL LIBRARY DATABASE \n"
        raise
