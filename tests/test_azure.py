from ocw.lib.azure import Azure, Provider
from webui.settings import PCWConfig
from datetime import datetime, timezone, timedelta
from .generators import MockImage
from .generators import mock_get_feature_property
from tests import generators
from msrest.exceptions import AuthenticationError
import time
import pytest


delete_calls = {'quantity': [], 'old': [], 'young': []}


def delete_blob_mock(self, container_name, img_name, snapshot=None):
    delete_calls[container_name].append(img_name)


def list_blobs_mock(self, container_name):
    last_modified = datetime.now()
    return [MockImage('SLES15-SP2-Azure-HPC.x86_64-0.9.0-Build1.43.vhd'),
            MockImage('SLES15-SP2-Azure-HPC.x86_64-0.9.1-Build1.3.vhd'),
            MockImage('SLES15-SP2-Azure-HPC.x86_64-0.9.1-Build1.7.vhd'),
            MockImage('SLES15-SP2-BYOS.x86_64-0.9.3-Azure-Build2.36.vhd', last_modified),
            MockImage('SLES15-SP2-BYOS.x86_64-0.9.6-Azure-Build1.3.vhd', last_modified),
            MockImage('SLES15-SP2-BYOS.x86_64-0.9.6-Azure-Build1.9.vhd', last_modified)
            ]


@pytest.fixture
def azure_patch(monkeypatch):
    monkeypatch.setattr(Provider, 'read_auth_json', lambda *args, **kwargs: '{}')
    monkeypatch.setattr(Azure, 'check_credentials', lambda *args, **kwargs: True)
    monkeypatch.setattr(PCWConfig, 'get_feature_property', mock_get_feature_property)
    return Azure('fake')


def test_parse_image_name(azure_patch):

    assert azure_patch.parse_image_name('SLES12-SP5-Azure.x86_64-0.9.1-SAP-BYOS-Build3.3.vhd') == {
        'key': '12-SP5-SAP-BYOS-x86_64',
        'build': '0.9.1-3.3'
    }

    assert azure_patch.parse_image_name('SLES15-SP2-BYOS.x86_64-0.9.3-Azure-Build1.10.vhd') == {
        'key': '15-SP2-Azure-BYOS-x86_64',
        'build': '0.9.3-1.10'
    }
    assert azure_patch.parse_image_name('SLES15-SP2.x86_64-0.9.3-Azure-Basic-Build1.11.vhd') == {
        'key': '15-SP2-Azure-Basic-x86_64',
        'build': '0.9.3-1.11'
    }

    assert azure_patch.parse_image_name('SLES15-SP2-SAP-BYOS.x86_64-0.9.2-Azure-Build1.9.vhd') == {
        'key': '15-SP2-Azure-SAP-BYOS-x86_64',
        'build': '0.9.2-1.9'
    }
    assert azure_patch.parse_image_name('SLES15-SP2-Azure-HPC.x86_64-0.9.0-Build1.43.vhd') == {
        'key': '15-SP2-Azure-HPC-x86_64',
        'build': '0.9.0-1.43'
    }
    assert azure_patch.parse_image_name('SLES15-SP2-BYOS.aarch64-0.9.3-Azure-Build2.36.vhdfixed.x') == {
        'key': '15-SP2-Azure-BYOS-aarch64',
        'build': '0.9.3-2.36'
    }

    assert azure_patch.parse_image_name('do not match') is None


def test_cleanup_sle_images_container(azure_patch, monkeypatch):
    class FakeContainerClient:
        deleted_blobs = list()

        def list_blobs(self):
            return [
                MockImage('SLES15-SP2-Azure-HPC.x86_64-0.9.0-Build1.43.vhd'),
                MockImage('SLES15-SP2-Azure-HPC.x86_64-0.9.1-Build1.3.vhd'),
                MockImage('YouWillNotGetMyBuildNumber'),
            ]

        def delete_blob(self, img_name, delete_snapshots):
            self.deleted_blobs.append(img_name)

    fakecontainerclient = FakeContainerClient()

    monkeypatch.setattr(Azure, 'container_client', lambda *args, **kwargs: fakecontainerclient)
    az = Azure('fake')
    keep_images = ['SLES15-SP2-Azure-HPC.x86_64-0.9.1-Build1.3.vhd']

    az.cleanup_sle_images_container(keep_images)
    assert fakecontainerclient.deleted_blobs == ['SLES15-SP2-Azure-HPC.x86_64-0.9.0-Build1.43.vhd']


def test_cleanup_images_from_rg(azure_patch, monkeypatch):
    deleted_images = list()
    items = [
        MockImage('SLES15-SP2-Azure-HPC.x86_64-0.9.0-Build1.43.vhd'),
        MockImage('SLES15-SP2-Azure-HPC.x86_64-0.9.1-Build1.3.vhd'),
        MockImage('YouWillNotGetMyBuildNumber'),
    ]

    def mock_res_mgmt_client(self):
        def res_mgmt_client():
            pass
        res_mgmt_client.resources = lambda: None
        res_mgmt_client.resources.list_by_resource_group = lambda *args, **kwargs: items
        return res_mgmt_client

    def mock_compute_mgmt_client(self):
        def compute_mgmt_client():
            pass
        compute_mgmt_client.images = lambda: None
        compute_mgmt_client.images.begin_delete = lambda rg, name: deleted_images.append(name)
        return compute_mgmt_client

    monkeypatch.setattr(Azure, 'resource_mgmt_client', mock_res_mgmt_client)
    monkeypatch.setattr(Azure, 'compute_mgmt_client', mock_compute_mgmt_client)

    az = Azure('fake')
    keep_images = ['SLES15-SP2-Azure-HPC.x86_64-0.9.1-Build1.3.vhd']
    az.cleanup_images_from_rg(keep_images)
    assert deleted_images == ['SLES15-SP2-Azure-HPC.x86_64-0.9.0-Build1.43.vhd']


def test_cleanup_disks_from_rg(azure_patch, monkeypatch):
    deleted_disks = list()

    items = [
        MockImage('SLES15-SP2-Azure-HPC.x86_64-0.9.0-Build1.43.vhd'),
        MockImage('SLES15-SP2-Azure-HPC.x86_64-0.9.1-Build1.3.vhd'),
        MockImage('SLES15-SP2-Azure-HPC.x86_64-0.9.1-Build1.7.vhd'),
        MockImage('YouWillNotGetMyBuildNumber'),
    ]

    def mock_res_mgmt_client(self):
        def res_mgmt_client():
            pass
        res_mgmt_client.resources = lambda: None
        res_mgmt_client.resources.list_by_resource_group = lambda *args, **kwargs: items
        return res_mgmt_client

    def mock_compute_mgmt_client(self):
        class FakeDisk:
            def __init__(self, rg, name):
                self.managed_by = name == 'SLES15-SP2-Azure-HPC.x86_64-0.9.0-Build1.43.vhd'

        def compute_mgmt_client():
            pass
        compute_mgmt_client.disks = lambda: None
        compute_mgmt_client.disks.get = lambda rg, name: FakeDisk(rg, name)
        compute_mgmt_client.disks.begin_delete = lambda rg, name: deleted_disks.append(name)
        return compute_mgmt_client

    monkeypatch.setattr(Azure, 'resource_mgmt_client', mock_res_mgmt_client)
    monkeypatch.setattr(Azure, 'compute_mgmt_client', mock_compute_mgmt_client)

    keep_images = ['SLES15-SP2-Azure-HPC.x86_64-0.9.1-Build1.3.vhd']
    az = Azure('fake')
    az.cleanup_disks_from_rg(keep_images)
    assert deleted_disks == ['SLES15-SP2-Azure-HPC.x86_64-0.9.1-Build1.7.vhd']


def test_get_keeping_image_names(azure_patch, monkeypatch):
    class FakeContainerClient:
        def list_blobs(self):
            older_then_min_age = datetime.now(timezone.utc) - timedelta(hours=generators.min_image_age_hours+1)
            return [
                MockImage('SLES15-SP2-Azure-HPC.x86_64-0.9.0-Build1.43.vhd', older_then_min_age),
                MockImage('SLES15-SP2-Azure-HPC.x86_64-0.9.1-Build1.3.vhd', older_then_min_age),
                MockImage('YouWillNotGetMyBuildNumber', older_then_min_age),
            ]

    fakecontainerclient = FakeContainerClient()
    monkeypatch.setattr(Azure, 'container_client', lambda *args, **kwargs: fakecontainerclient)

    az = Azure('fake')
    generators.max_images_per_flavor = 1
    assert az.get_keeping_image_names() == ['SLES15-SP2-Azure-HPC.x86_64-0.9.1-Build1.3.vhd']


def test_cleanup_all(azure_patch, monkeypatch):
    called = 0

    def count_call(*args, **kwargs):
        nonlocal called
        called = called + 1

    monkeypatch.setattr(Azure, 'get_storage_key', lambda *args, **kwargs: 'FOOXX')
    monkeypatch.setattr(Azure, 'get_keeping_image_names', lambda *args, **kwargs: ['a', 'b'])
    monkeypatch.setattr(Azure, 'cleanup_sle_images_container', count_call)
    monkeypatch.setattr(Azure, 'cleanup_disks_from_rg', count_call)
    monkeypatch.setattr(Azure, 'cleanup_images_from_rg', count_call)
    monkeypatch.setattr(Azure, 'cleanup_bootdiagnostics', count_call)
    monkeypatch.setattr(Provider, 'read_auth_json', lambda *args, **kwargs: '{}')

    az = Azure('fake')
    az.cleanup_all()
    assert called == 4


def test_cleanup_bootdiagnostics(azure_patch, monkeypatch):

    called = 0

    def count_call(*args, **kwargs):
        nonlocal called
        called = called + 1

    class FakeBlobServiceClient:

        def list_containers(self):
            return [
                MockImage('bootdiagnostics-A'),
                MockImage('ShouldNotMatchRegex'),
                MockImage('bootdiagnostics-C'),
                MockImage('bootdiagnostics-D'),
                MockImage('bootdiagnostics-E'),
            ]

    fakeblobserviceclient = FakeBlobServiceClient()

    monkeypatch.setattr(Azure, 'bs_client', lambda *args, **kwargs: fakeblobserviceclient)
    monkeypatch.setattr(Azure, 'cleanup_bootdiagnostics_container', count_call)

    az = Azure('fake')
    az.cleanup_bootdiagnostics()

    assert called == 4


def test_cleanup_bootdiagnostics_container_older_than_min_age(azure_patch, monkeypatch):

    class FakeBlobServiceClient:
        deleted_containers = list()

        def delete_container(self, container_name):
            self.deleted_containers.append(container_name)

    class FakeContainerClient():

        def list_blobs(self):
            older_then_min_age = datetime.now(timezone.utc) - timedelta(hours=generators.min_image_age_hours+1)
            newer_then_min_age = datetime.now(timezone.utc)
            return [
                MockImage('Image', newer_then_min_age),
                MockImage('Image', newer_then_min_age),
                MockImage('Image', newer_then_min_age),
                MockImage('Image', older_then_min_age),
            ]

    fakecontainerclient = FakeContainerClient()
    fakeblobserviceclient = FakeBlobServiceClient()
    monkeypatch.setattr(Azure, 'container_client', lambda *args, **kwargs: fakecontainerclient)
    monkeypatch.setattr(Azure, 'bs_client', lambda *args, **kwargs: fakeblobserviceclient)

    az = Azure('fake')
    az.cleanup_bootdiagnostics_container(MockImage('HaveOneOlder', datetime.now(timezone.utc)))
    assert len(fakeblobserviceclient.deleted_containers) == 1


def test_cleanup_bootdiagnostics_container_all_newer(azure_patch, monkeypatch):

    class FakeBlobServiceClient:
        deleted_containers = list()

        def delete_container(self, container_name):
            self.deleted_containers.append(container_name)

    class FakeContainerClient():

        def list_blobs(self):
            older_then_min_age = datetime.now(timezone.utc) - timedelta(hours=generators.min_image_age_hours+1)
            newer_then_min_age = datetime.now(timezone.utc)
            return [
                MockImage('Image', newer_then_min_age),
                MockImage('Image', newer_then_min_age),
                MockImage('Image', newer_then_min_age),
                MockImage('Image', newer_then_min_age),
            ]

    fakecontainerclient = FakeContainerClient()
    fakeblobserviceclient = FakeBlobServiceClient()
    monkeypatch.setattr(Azure, 'container_client', lambda *args, **kwargs: fakecontainerclient)
    monkeypatch.setattr(Azure, 'bs_client', lambda *args, **kwargs: fakeblobserviceclient)

    az = Azure('fake')
    az.cleanup_bootdiagnostics_container(MockImage('AllNewer', datetime.now(timezone.utc)))
    assert len(fakeblobserviceclient.deleted_containers) == 0


def test_check_credentials(monkeypatch):
    count_list_resource_groups = 0
    failed_list_resource_groups = 0

    def mock_list_resource_groups(self):
        nonlocal count_list_resource_groups
        count_list_resource_groups = count_list_resource_groups + 1
        if count_list_resource_groups > failed_list_resource_groups:
            return True
        raise AuthenticationError("OHA Mocked auth error")

    monkeypatch.setattr(Azure, 'list_resource_groups', mock_list_resource_groups)
    monkeypatch.setattr(time, 'sleep', lambda *args, **kwargs: True)
    monkeypatch.setattr(Provider, 'read_auth_json', lambda *args, **
                        kwargs: {'client_id': 'fake'})
    monkeypatch.setattr(PCWConfig, 'get_feature_property', mock_get_feature_property)

    count_list_resource_groups = 0
    failed_list_resource_groups = 3
    az = Azure('fake')
    assert count_list_resource_groups == 4

    count_list_resource_groups = 0
    failed_list_resource_groups = 5
    with pytest.raises(AuthenticationError):
        az = Azure('fake')
