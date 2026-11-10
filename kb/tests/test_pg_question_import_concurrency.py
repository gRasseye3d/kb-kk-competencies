"""Separate empty kb_it_* database: real two-backend stage-3 approval race."""
import os
import unittest

import test_pg_testing_integration as integration
import test_pg_testing_concurrency as concurrency


@unittest.skipUnless(os.environ.get('DATABASE_URL') and os.environ.get('KB_IT_CONCURRENCY') == '1',
                     'Requires a separate new kb_it_* database and KB_IT_CONCURRENCY=1')
class ImportApprovalRacePGTests(unittest.TestCase):
    setUpClass = classmethod(integration.PublishedTestApiIntegration.setUpClass.__func__)
    row = concurrency.RealConnectionConcurrencyTests.row
    seed = concurrency.RealConnectionConcurrencyTests.seed
    call = concurrency.RealConnectionConcurrencyTests.call
    api = concurrency.RealConnectionConcurrencyTests.api
    request = concurrency.RealConnectionConcurrencyTests.request
    test_import_double_approval_independent_connections = concurrency.RealConnectionConcurrencyTests.import_double_approval_independent_connections
