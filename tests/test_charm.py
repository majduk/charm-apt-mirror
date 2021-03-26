# Copyright 2020 Ubuntu
# See LICENSE file for licensing details.

import unittest
from unittest.mock import (
    Mock,
    patch,
    mock_open,
    call
)
from uuid import uuid4
from ops.testing import Harness
from charm import AptMirrorCharm
import random
import os


class TestCharm(unittest.TestCase):

    def default_config(self):
        return {
            'mirror-list': "deb {}\ndeb {}".format(uuid4(), uuid4()),
            'base-path': uuid4(),
            'architecture': uuid4(),
            'threads': random.randint(10, 20),
        }

    @patch('builtins.open', new_callable=mock_open)
    def test_publish_relation_joined(self, mock_open_call):
        harness = Harness(AptMirrorCharm)
        harness.begin()
        default_config = self.default_config()
        self.assertEqual(harness.charm._stored.config, {})
        harness.update_config(default_config)
        relation_id = harness.add_relation('publish', 'webserver')
        harness.add_relation_unit(relation_id, 'webserver/0')
        assert harness.get_relation_data(relation_id, harness._unit_name)\
               == {'path': '{}/publish'.format(default_config['base-path'])}

    @patch('subprocess.check_output')
    def test_install(self, mock_subproc):
        process_mock = Mock()
        mock_subproc.return_value = process_mock
        harness = Harness(AptMirrorCharm)
        harness.begin()
        action_event = Mock()
        harness.charm._on_install(action_event)
        self.assertTrue(mock_subproc.called)
        assert mock_subproc.call_args == call(["apt", "install", "-y", "apt-mirror"])

    @patch('builtins.open', new_callable=mock_open)
    def test_cron_schedule_set(self, mock_open_call):
        harness = Harness(AptMirrorCharm)
        self.addCleanup(harness.cleanup)
        harness.begin()
        schedule = uuid4()
        default_config = {
            'cron-schedule': schedule,
        }
        self.assertEqual(harness.charm._stored.config, {})
        harness.update_config(default_config)
        mock_open_call.assert_called_with('/etc/cron.d/{}'.format(harness.charm.model.app.name),
                                          "w")
        mock_open_call.return_value.write.assert_called_with(
            "{} root apt-mirror\n".format(schedule)
        )

    def test_apt_mirror_list(self):
        with open('templates/mirror.list.j2') as f:
            t = f.read()
        mock_open_call = mock_open(read_data=t)
        harness = Harness(AptMirrorCharm)
        self.addCleanup(harness.cleanup)
        harness.begin()
        default_config = self.default_config()
        self.assertEqual(harness.charm._stored.config, {})
        with patch('builtins.open', mock_open_call):
            harness.update_config(default_config)
        mock_open_call.assert_called_with('/etc/apt/mirror.list', "wb")
        mock_open_call.return_value.write.assert_called_once_with(
            'set base_path         {base-path}\n'
            'set mirror_path       $base_path/mirror\n'
            'set skel_path         $base_path/skel\n'
            'set var_path          $base_path/var\n'
            'set postmirror_script $var_path/postmirror.sh\n'
            'set defaultarch       {architecture}\n'
            'set run_postmirror    0\n'
            'set nthreads          {threads}\n'
            'set limit_rate        100m\n'
            'set _tilde            0\n'
            '{mirror-list}\n'.format(**default_config).encode()
        )

    @patch.dict(os.environ, {"JUJU_CHARM_HTTP_PROXY": "httpproxy",
                             "JUJU_CHARM_HTTPS_PROXY": "httpsproxy"},
                clear=True)
    def test_juju_proxy(self):
        with open('templates/mirror.list.j2') as f:
            t = f.read()
        mock_open_call = mock_open(read_data=t)
        harness = Harness(AptMirrorCharm)
        self.addCleanup(harness.cleanup)
        harness.begin()
        default_config = self.default_config()
        self.assertEqual(harness.charm._stored.config, {})
        with patch('builtins.open', mock_open_call):
            harness.update_config(default_config)
        mock_open_call.assert_called_with('/etc/apt/mirror.list', "wb")
        mock_open_call.return_value.write.assert_called_once_with(
            'set base_path         {base-path}\n'
            'set mirror_path       $base_path/mirror\n'
            'set skel_path         $base_path/skel\n'
            'set var_path          $base_path/var\n'
            'set postmirror_script $var_path/postmirror.sh\n'
            'set defaultarch       {architecture}\n'
            'set run_postmirror    0\n'
            'set nthreads          {threads}\n'
            'set limit_rate        100m\n'
            'set _tilde            0\n'
            'set use_proxy         on\n'
            'set http_proxy        httpproxy\n'
            'set https_proxy       httpsproxy\n'
            '{mirror-list}\n'.format(**default_config).encode()
        )

    @patch.dict(os.environ, {"JUJU_CHARM_HTTP_PROXY": "httpproxy",
                             "JUJU_CHARM_HTTPS_PROXY": "httpsproxy"},
                clear=True)
    def test_juju_proxy_override(self):
        with open('templates/mirror.list.j2') as f:
            t = f.read()
        mock_open_call = mock_open(read_data=t)
        harness = Harness(AptMirrorCharm)
        self.addCleanup(harness.cleanup)
        harness.begin()
        default_config = self.default_config()
        default_config['use-proxy'] = False
        self.assertEqual(harness.charm._stored.config, {})
        with patch('builtins.open', mock_open_call):
            harness.update_config(default_config)
        mock_open_call.assert_called_with('/etc/apt/mirror.list', "wb")
        mock_open_call.return_value.write.assert_called_once_with(
            'set base_path         {base-path}\n'
            'set mirror_path       $base_path/mirror\n'
            'set skel_path         $base_path/skel\n'
            'set var_path          $base_path/var\n'
            'set postmirror_script $var_path/postmirror.sh\n'
            'set defaultarch       {architecture}\n'
            'set run_postmirror    0\n'
            'set nthreads          {threads}\n'
            'set limit_rate        100m\n'
            'set _tilde            0\n'
            '{mirror-list}\n'.format(**default_config).encode()
        )

    @patch('subprocess.check_output')
    def test_synchronize_action(self, mock_subproc):
        process_mock = Mock()
        mock_subproc.return_value = process_mock
        harness = Harness(AptMirrorCharm)
        harness.begin()
        action_event = Mock()
        harness.charm._on_synchronize_action(action_event)
        self.assertTrue(mock_subproc.called)
        assert mock_subproc.call_args == call(['apt-mirror'], stderr=-2)

    @patch('os.walk')
    @patch('shutil.copytree')
    @patch('os.path.exists')
    @patch('os.symlink')
    @patch('os.makedirs')
    def test_create_snapshot_action(self, os_makedirs, os_symlink, os_path_exists,
                                    shutil_copytree, os_walk):
        def a2g(x):
            return ([n, ['{}'.format(n)]] for n in x)

        rand_n = random.randint(10, 100)
        rand_m = random.randint(10, 100)
        os_walk.side_effect = iter([a2g([rand_n]), a2g([rand_m])])
        os_path_exists.return_value = False
        default_config = self.default_config()
        harness = Harness(AptMirrorCharm)
        harness.begin()
        harness.charm._stored.config = default_config
        harness.charm._get_snapshot_name = Mock()
        snapshot_name = uuid4()
        harness.charm._get_snapshot_name.return_value = snapshot_name
        action_event = Mock()
        harness.charm._on_create_snapshot_action(action_event)
        self.assertTrue(os_symlink.called)
        assert os_symlink.call_args == call('{}/mirror/{}/{}/pool'
                                            .format(default_config['base-path'], rand_n, rand_m),
                                            '{}/{}/{}/{}/pool'.format(default_config['base-path'],
                                                                      snapshot_name, rand_n,
                                                                      rand_m))

    @patch('os.walk')
    def test_list_snapshots_action(self, os_walk):
        def a2g(x):
            return ([n, ['snapshot-{}'.format(n)]] for n in x)

        rand_n = random.randint(10, 100)
        os_walk.return_value = a2g([rand_n])
        default_config = self.default_config()
        harness = Harness(AptMirrorCharm)
        harness.begin()
        harness.charm._stored.config = default_config
        harness.charm._get_snapshot_name = Mock()
        action_event = Mock()
        harness.charm._on_list_snapshots_action(action_event)
        assert action_event.set_results.call_args == call({"snapshots":
                                                           ['snapshot-{}'.format(rand_n)]})

    @patch('shutil.rmtree')
    def test_delete_snapshot_action(self, shutil_rmtree):
        default_config = self.default_config()
        harness = Harness(AptMirrorCharm)
        harness.begin()
        harness.charm._stored.config = default_config
        harness.charm._get_snapshot_name = Mock()
        snapshot_name = uuid4()
        action_event = Mock(params={"name": snapshot_name})
        harness.charm._on_delete_snapshot_action(action_event)
        assert shutil_rmtree.call_args == call('{}/{}'.format(default_config['base-path'],
                                                              snapshot_name))

    @patch('os.path.isdir')
    @patch('os.path.islink')
    @patch('os.symlink')
    @patch('os.unlink')
    def test_publish_snapshot_action_success(self, os_unlink, os_symlink, os_path_islink,
                                             os_path_isdir):
        default_config = self.default_config()
        harness = Harness(AptMirrorCharm)
        harness.begin()
        harness.charm._stored.config = default_config
        harness.charm._get_snapshot_name = Mock()
        snapshot_name = uuid4()
        action_event = Mock(params={"name": snapshot_name})
        harness.charm._on_publish_snapshot_action(action_event)
        assert os_symlink.call_args == call('{}/{}'.format(default_config['base-path'],
                                                           snapshot_name),
                                            '{}/publish'.format(default_config['base-path']))

    @patch('os.path.isdir')
    @patch('os.path.islink')
    @patch('os.symlink')
    @patch('os.unlink')
    def test_publish_snapshot_action_fail(self, os_unlink, os_symlink, os_path_islink,
                                          os_path_isdir):
        os_path_isdir.return_value = False
        default_config = self.default_config()
        harness = Harness(AptMirrorCharm)
        harness.begin()
        harness.charm._stored.config = default_config
        harness.charm._get_snapshot_name = Mock()
        snapshot_name = uuid4()
        action_event = Mock(params={"name": snapshot_name})
        harness.charm._on_publish_snapshot_action(action_event)
        assert action_event.fail.call_args == call('Snapshot does not exist')
