# -*- coding: utf-8 -*-

from django.core.exceptions import ImproperlyConfigured, ObjectDoesNotExist
from django.db.models import Model

from .models import AppliedMigration

import inspect
import sys

def is_a(klass, search_attr, fk=False, m2m=False, o2o=False,
                exclude=False, delimiter=';', skip_missing=False):

    if search_attr is None:
        raise ImproperlyConfigured(
            'is_a(%s) requires that you set a `search_attr`' % klass.__name__)

    return { 'm2m': m2m, 'klass': klass, 'fk': fk, 'o2o': o2o,
             'attr': search_attr, 'exclude': exclude, 'delimiter': delimiter,
             'skip_missing': skip_missing,
            }


class Migration(object):
    """Baseclass for the data migration"""

    abstract = False
    skip = False

    # Database settings, should be right for most of uses
    db_host = None
    db_user = None
    db_password = None

    # model class the migration creates instances for
    model = None

    # SQL SELECT query which returns the data suitable for the new model
    # structure
    query = None

    # this should be a dict which describes the data returned by the query. You
    # can supply the Class that the data should be
    column_description = {}

    # a list of classes that the model requires to be migrated before
    # The User class for example is included into many other models
    depends_on = []

    # If the following is set to False, the migration will be executed only once
    # Otherwise it will create missing elements
    allow_updates = False

    # this is a unique model field, which is used to search for existing
    # model instances
    #
    # Example: for Django`s User model it is `username`
    #
    # REQUIRED if `allow_updates` is set to True
    search_attr = None

    #########
    # Hooks #
    #########

    # This function will be called for each row the query returns
    # after the model has been instantiated or saved.
    #
    # Parameters:
    #   1. the current model instance
    #   2. The current row returned from the query
    @classmethod
    def hook_before_transformation(self, row):
        pass

    @classmethod
    def hook_before_save(self, instance, row):
        pass

    @classmethod
    def hook_after_save(self, instance, row):
        pass

    @classmethod
    def hook_update_existing(self, instance, row):
        """
        Is called on the existing instance when `self.allow_updates` is True

        `row` contains the raw result without any transformation

        Is is YOUR responsibility to make sure, that this method can be called
        MULTIPLE times. DO SOME CHECKS
        """
        pass


    # hooks that will be called [before|after] all
    # data [has been|will be] migrated
    #
    # BUT NOT FOR MIGRATIONS THAT WILL BE UPDATED
    @classmethod
    def hook_before_all(self):
        pass

    @classmethod
    def hook_after_all(self):
        pass

    ###################
    # INTERNAL THINGS #
    ###################
    @classmethod
    def migrate(self):
        """method that is called to migrate this migration"""

        check = self.migration_required()
        if check == False:
            print "%s has already been migrated, skip it!" % self
            return None

        print "Migrating %s" % self

        self.check_migration() # check the configuration of the Migration
        connection = self.open_db_connection()

        cursor = connection.cursor()
        cursor.execute(self.query)
        fields = [ row[0] for row in cursor.description ]

        if check is None:
            # update existing migrations
            self.process_cursor_for_update(cursor, fields)

        else:
            # do the normal migration method
            self.process_cursor(cursor, fields)

            AppliedMigration.objects.create(classname=unicode(self))


    @classmethod
    def open_db_connection(self):
        raise ImproperlyConfigured(
            "You have to supply a suitable db connection for your DB")


    @classmethod
    def process_cursor(self, cursor, fields):
        total = cursor.rowcount
        current = 0

        self.hook_before_all()

        for row in cursor.fetchall():

            current += 1
            sys.stdout.write("\rMigrating element %d/%d" % (current, total))
            sys.stdout.flush()

            self.hook_before_transformation(row)
            constructor_data, m2ms = self.transform_row_dataset(row)
            instance = self.model(**constructor_data)

            self.hook_before_save(instance, row)

            instance.save()

            self.create_m2ms(instance, m2ms)

            self.hook_after_save(instance, row)

        self.hook_after_all()
        print ""


    @classmethod
    def process_cursor_for_update(self, cursor, fields):
        total = cursor.rowcount
        created = 0
        existing = 0

        for row in cursor.fetchall():

            # search for an existing instance
            desc = is_a(self.model, search_attr=self.search_attr, skip_missing=True)
            element = self.get_object(desc, row[self.search_attr])

            if element is not None:
                existing += 1
            else:
                created += 1

            sys.stdout.write(
                "\rSearch for missing Instances (exist/created/total):  %d/%d/%d" % (
                    existing, created, total))
            sys.stdout.flush()

            if element is not None:
                self.hook_update_existing(element, row)
                continue

            self.hook_before_transformation(row)
            constructor_data, m2ms = self.transform_row_dataset(row)
            instance = self.model(**constructor_data)

            self.hook_before_save(instance, row)

            instance.save()

            self.create_m2ms(instance, m2ms)

            self.hook_after_save(instance, row)

        print ""


    @classmethod
    def transform_row_dataset(self, datarow):
        """transforms the supplied row and evaluates columns of different types

        returns the dict where columns which FKs or Data has been updated with
        real instances
        """
        constructor_data = {}
        m2ms = {}

        for fieldname, data in datarow.iteritems():
            if self.column_description.has_key(fieldname):
                desc = self.column_description[fieldname]

                if desc['exclude'] == True:
                    continue

                elif desc['fk'] == True or desc['o2o'] == True:
                    instance = self.get_object(desc, data)
                    constructor_data[fieldname] = instance

                elif desc['m2m'] == True:
                    if data is None:
                        continue

                    usernames = data.split(desc['delimiter'])
                    users = []

                    for name in usernames:
                        element = self.get_object(desc, name)
                        if element is None:
                            continue
                        users.append(element)
                    m2ms[fieldname] = users

            else:
                constructor_data[fieldname] = data

        return (constructor_data, m2ms,)


    @classmethod
    def get_object(self, desc, value):
        criteria = { desc['attr']: value }
        try:
            return desc['klass'].objects.get(**criteria)
        except self.model.DoesNotExist, e:
            if desc['skip_missing']:
                return None
            else:
                raise


    @classmethod
    def create_m2ms(self, instance, m2ms):
        for field, values in m2ms.iteritems():
            instance.__getattribute__(field).add(*values)


    @classmethod
    def migration_required(self):
        """checks if the migration has already been applied"""
        try:
            AppliedMigration.objects.get(classname=unicode(self))

            if self.allow_updates:
                return None

            return False
        except AppliedMigration.DoesNotExist, e:
            return True


    @classmethod
    def check_migration(self):

        if not isinstance(self.column_description, dict):
            raise ImproperlyConfigured(
                    '%s: `column_description` has to be a dict' % self)

        if self.allow_updates and self.search_attr is None:
            raise ImproperlyConfigured(
                    '%s: `allow_updates` forces you to set the `search_attr` on ' % self +
                    'the Migration. to search for existing instances. Example: `username`')

        if not isinstance(self.depends_on, list):
            raise ImproperlyConfigured(
                    '%s: `depends_on` has to be a list of classes' % self)

        if not ( inspect.isclass(self.model) and issubclass(self.model, Model)):
            raise ImproperlyConfigured(
                    '%s: `model` has to be a model CLASS' % self)

        if not ( isinstance(self.query, str) and "SELECT" in self.query ):
            raise ImproperlyConfigured(
                    '%s: `model` has to be a string containing SELECT' % self)