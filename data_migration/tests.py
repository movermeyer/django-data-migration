# -*- coding: utf-8 -*-
from __future__ import unicode_literals
from future.builtins import str

from django.test import TestCase, TransactionTestCase
from django.core.exceptions import ImproperlyConfigured
from django.conf import settings
from django.contrib.auth.models import User, Group

from mock import patch
from io import StringIO

from .models import AppliedMigration
from .migration import is_a, Migration, Importer, Migrator

import sys

"""
Utility stuff
"""
def raise_(ex):
    raise ex

def install_apps(apps):

    apps = [ "data_migration.test_apps.%s" % app for app in apps ]

    def real_decorator(function):
        def wrapper(*args, **kwargs):
            with patch.object(Importer, 'installed_apps') as method:
                method.return_value = apps
                function(*args, **kwargs)

        return wrapper
    return real_decorator


def run_migrations(*migrations):
    """ this decorator should be placed on top of all other decorators as it
    calls methods on migrations, which could be patched by decorators called
    after it
    """
    def real_decorator(function):
        def wrapper(*args, **kwargs):
            with patch.object(Migration, '__subclasses__') as method:

                # cleanup relation caches as it could be compromised by
                # previous tests
                for mig in migrations:
                    mig.cleanup_relation_cache()

                method.return_value = migrations
                function(*args, **kwargs)
        return wrapper
    return real_decorator

"""
Test Cases
"""
class ImporterTest(TestCase):

    @install_apps(['valid_a', 'valid_b' 'missing_spec'])
    def test_import_existing_migrations_with_respect_to_excludes(self):
        old_count = len(Migration.__subclasses__())
        Importer.import_all(excludes=["valid_b"])
        new_count = len(Migration.__subclasses__())

        self.assertEqual(new_count - old_count, 1)


from .test_apps.blog.models import Author, Post, Comment
from .test_apps.blog.data_migration_spec import *

class MigratorTest(TransactionTestCase):

    @install_apps(['valid_a', 'blog'])
    def test_that_no_abstract_migration_will_be_sorted_in(self):
        Importer.import_all()

        _sorted = Migrator.sorted_migrations()
        self.assertFalse(BaseMigration in _sorted)


    @install_apps(['valid_a', 'blog'])
    def test_topological_sorting(self):
        Importer.import_all()

        _sorted = Migrator.sort_based_on_dependency(
                    [AuthorMigration, PostMigration, CommentMigration])
        self.assertEqual(_sorted[0].model, Author)
        self.assertEqual(_sorted[1].model, Comment)
        self.assertEqual(_sorted[2].model, Post)


    @patch.object(Migrator, 'sorted_migrations')
    @patch('sys.stderr', new_callable=StringIO)
    def test_transaction_handling(self, stderr, sorted_migrations):
        sorted_migrations.return_value = [ AuthorMigration ]

        AuthorMigration.migrate = classmethod(
            lambda cls: AppliedMigration.objects.create(classname="test"))

        Migrator.migrate(commit=False)
        self.assertEqual(AppliedMigration.objects.count(), 0)

        Migrator.migrate(commit=True)
        self.assertEqual(AppliedMigration.objects.count(), 1)


class IsATest(TestCase):

    def test_normal_description(self):
        self.assertEqual(is_a(User, 'username', fk=True), {
            'klass': User,
            'attr': 'username',
            'm2m': False,
            'delimiter': u';',
            'skip_missing': False,
            'o2o': False,
            'exclude': False,
            'fk': True,
            'prefetch': True,
            'assign_by_id': False
        })

    def test_that_class_and_attr_has_to_be_present(self):
        with self.assertRaises(ImproperlyConfigured):
            is_a(fk=True)

    def test_that_class_has_to_be_a_model(self):
        with self.assertRaises(ImproperlyConfigured):
            is_a(str(User), 'username', fk=True)

    def test_multiple_type_definition(self):
        with self.assertRaises(ImproperlyConfigured):
            is_a(User, 'username', fk=True, m2m=True)

    def test_exclude_from_processing(self):
        self.assertEqual(is_a(exclude=True), {
            'klass': None,
            'attr': None,
            'm2m': False,
            'delimiter': u';',
            'skip_missing': False,
            'o2o': False,
            'exclude': True,
            'fk': False,
            'prefetch': True,
            'assign_by_id': False
        })

    def test_performance_options(self):
        attr = is_a(User, 'username', fk=True, assign_by_id=True)
        self.assertEqual(attr['prefetch'], True)
        self.assertEqual(attr['assign_by_id'], True)

    def test_assign_by_id_is_only_allowed_with_prefetching(self):
        with self.assertRaises(ImproperlyConfigured):
            is_a(User, 'username', fk=True, prefetch=False, assign_by_id=True)


from datetime import datetime
from django.core import management

import os
import sqlite3

class MigrationTest(TransactionTestCase):

    def setUp(self):
        super(TransactionTestCase, self).setUp()

        self.db_path = os.path.join(
                os.path.dirname(__file__), 'test_apps/blog/blog_fixture.db')

        if not os.path.isfile(self.db_path):
            fixture = os.path.join(os.path.dirname(self.db_path), "fixtures.sql")
            conn = sqlite3.connect(self.db_path)
            conn.cursor().executescript(open(fixture).read())
            conn.close()


    def tearDown(self):
        super(TransactionTestCase, self).tearDown()

        if os.path.isfile(self.db_path):
            os.unlink(self.db_path)


    @run_migrations(AuthorMigration, PostMigration, CommentMigration)
    @patch('sys.stdout', new_callable=StringIO)
    def test_normal_migration(self, stdout):
        Migrator.migrate(commit=True)

        self.assertEqual(Author.objects.count(), 10)
        self.assertEqual(Comment.objects.count(), 20)
        self.assertEqual(Post.objects.count(), 10)

        post9 = Post.objects.get(id=9)
        self.assertEqual(post9.comments.count(), 3)
        self.assertEqual(post9.title,
                "lacinia at, iaculis quis, pede. Praesent eu dui. Cum sociis")
        self.assertEqual(post9.posted, datetime(2014, 10, 13, 8, 36, 59))


    @run_migrations(AuthorMigration)
    @patch.object(AuthorMigration, 'hook_update_existing')
    @patch.object(AuthorMigration, 'hook_after_all')
    @patch.object(AuthorMigration, 'hook_after_save')
    @patch.object(AuthorMigration, 'hook_before_transformation')
    @patch.object(AuthorMigration, 'hook_before_all')
    @patch('sys.stdout', new_callable=StringIO)
    def test_hook_calling(self, stdout, bef_all, bef_trans,
                          aft_save, aft_all, exist):
        Migrator.migrate(commit=True)

        self.assertFalse(exist.called)

        methods = [ bef_all, bef_trans, aft_save, aft_all ]
        for method in methods:
            self.assertTrue(method.called)

        self.assertEqual(AppliedMigration.objects.count(), 1)


    @run_migrations(AuthorMigration)
    @patch.object(AuthorMigration, 'hook_after_save')
    @patch.object(AuthorMigration, 'hook_update_existing')
    @patch('sys.stdout', new_callable=StringIO)
    def test_updatable_migrations(self, stdout, exist, aft_save):
        Migrator.migrate(commit=True)

        Author.objects.get(id=10).delete()
        self.assertFalse(exist.called)
        self.assertEqual(Author.objects.count(), 9)

        Migrator.migrate(commit=True)
        self.assertTrue(exist.called)
        self.assertEqual(exist.call_count, 9)
        self.assertEqual(aft_save.call_count, 11)
        self.assertEqual(Author.objects.count(), 10)


    @run_migrations(AuthorMigration)
    @patch.object(AuthorMigration, 'hook_row_count')
    @patch('sys.stdout', new_callable=StringIO)
    @patch('sys.stderr', new_callable=StringIO)
    def test_row_count_hook(self, err, out, hook):
        hook.side_effect = lambda conn, cursor: 55555

        Migrator.migrate(commit=True)
        self.assertTrue(hook.called)

        connection, cursor = hook.call_args[0]
        self.assertEqual(cursor.rowcount, -1)
        self.assertTrue("1/55555" in out.getvalue())


    @run_migrations(AuthorMigration, CommentMigration)
    @patch.object(CommentMigration, 'hook_before_save')
    @patch('sys.stderr', new_callable=StringIO)
    @patch('sys.stdout', new_callable=StringIO)
    def test_error_handling_default_behaviour(self, out, err, hook):
        hook.side_effect = lambda instance, row: raise_(ValueError())

        with self.assertRaises(ValueError):
            Migrator.migrate(commit=True)

        output = err.getvalue()
        self.assertTrue("Error: The following row produces an" in output)


    @run_migrations(AuthorMigration, CommentMigration)
    @patch.object(CommentMigration, 'hook_error_creating_instance')
    @patch.object(CommentMigration, 'hook_before_save')
    @patch('sys.stdout', new_callable=StringIO)
    @patch('sys.stderr', new_callable=StringIO)
    def test_error_handling_hook_is_called(self, err, out, hook, error):
        hook.side_effect = lambda instance, row: raise_(ValueError())
        error.side_effect = None

        Migrator.migrate(commit=True)

        # test that the right parameters re passed to the hook
        error.assert_called()
        exception, row = error.call_args[0]
        self.assertTrue(isinstance(exception, ValueError))
        self.assertTrue(isinstance(row, dict))


    @run_migrations(AuthorMigration, CommentMigration)
    @patch('sys.stdout', new_callable=StringIO)
    @patch('sys.stderr', new_callable=StringIO)
    @patch.object(CommentMigration, 'cleanup_relation_cache')
    @patch.object(Author.objects, 'get')
    def test_prefetching_fk(self, get, clean, err, out):
        with patch.dict(CommentMigration.column_description, {
            'author': is_a(Author, search_attr="id", fk=True, prefetch=True)}):

            Migrator.migrate(commit=True)
            self.assertEqual(get.call_count, 0)
            clean.assert_called
            self.assertEqual(len(CommentMigration.relation_cache[Author]), 10)

    @run_migrations(AuthorMigration, CommentMigration, PostMigration)
    @patch('sys.stdout', new_callable=StringIO)
    @patch('sys.stderr', new_callable=StringIO)
    @patch.object(PostMigration, 'cleanup_relation_cache')
    @patch.object(Comment.objects, 'get')
    def test_prefetching_m2m(self, get, clean, err, out):
        with patch.dict(PostMigration.column_description, {
            'author': is_a(Author, search_attr="id", fk=True),
            'comments':
                is_a(Comment, search_attr="id", m2m=True, delimiter=",", prefetch=True)
            }):

            Migrator.migrate(commit=True)
            self.assertEqual(get.call_count, 0)
            clean.assert_called


    @run_migrations(AuthorMigration, CommentMigration)
    @patch('sys.stdout', new_callable=StringIO)
    @patch('sys.stderr', new_callable=StringIO)
    @patch.object(CommentMigration, 'cleanup_relation_cache')
    @patch.object(CommentMigration, 'hook_before_save')
    def test_assign_by_id_fk(self, before_save, clean, err, out):

        with patch.dict(CommentMigration.column_description, {
            'author': is_a(Author, search_attr="id", fk=True,
                           assign_by_id=True, prefetch=True)}):

            def side_effect(instance, row):
                assert isinstance(instance.author_id, int)

            before_save.side_effect = side_effect
            Migrator.migrate(commit=True)
            for val in CommentMigration.relation_cache[Author].values():
                self.assertTrue(isinstance(val, int))


    @run_migrations(AuthorMigration, CommentMigration, PostMigration)
    @patch('sys.stdout', new_callable=StringIO)
    @patch('sys.stderr', new_callable=StringIO)
    @patch.object(Comment.objects, 'get')
    @patch.object(PostMigration, 'cleanup_relation_cache')
    def test_assign_by_id_m2m(self, clean, get, err, out):
        with patch.dict(PostMigration.column_description, {
            'author': is_a(Author, search_attr="id", fk=True, prefetch=True),
            'comments':
                is_a(Comment, search_attr="id", m2m=True, delimiter=",",
                     prefetch=True, assign_by_id=True)
            }):

            Migrator.migrate(commit=True)
            self.assertEqual(get.call_count, 0)
            for val in PostMigration.relation_cache[Comment].values():
                self.assertTrue(isinstance(val, int))


    @run_migrations(AuthorMigration)
    @patch('sys.stderr', new_callable=StringIO)
    @patch('sys.stdout', new_callable=StringIO)
    def test_calling_management_command(self, stdout, stderr):
        management.call_command('migrate_legacy_data', commit_changes=True)

        val = stderr.getvalue()
        self.assertFalse("is deprecated in favour of" in val)
        self.assertFalse("Not commiting! No changes" in val)
        self.assertTrue("Migrating element" in stdout.getvalue())


    @run_migrations(AuthorMigration)
    @patch('sys.stderr', new_callable=StringIO)
    @patch('sys.stdout', new_callable=StringIO)
    def test_calling_deprecated_management_command(self, stdout, stderr):
        management.call_command('migrate_this_shit', commit_changes=True)

        val = stderr.getvalue()
        self.assertTrue("is deprecated in favour of" in val)
        self.assertFalse("Not commiting! No changes" in val)
        self.assertTrue("Migrating element" in stdout.getvalue())


    @run_migrations(AuthorMigration, CommentMigration)
    @patch.object(AuthorMigration, 'hook_after_all')
    @patch('sys.stdout', new_callable=StringIO)
    def test_skip_missing(self, stdout, aft_all):
        aft_all.side_effect = lambda: Author.objects.get(id=3).delete()

        # there shouldn't be an exception here (when skip_missing works)
        Migrator.migrate(commit=True)
