import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from pegasus_bridge.core import BridgeError
from pegasus_bridge.installation import client_config, doctor_mcp


class InstallationTests(unittest.TestCase):
    def test_json_uses_installed_interpreter_and_no_credentials(self):
        with patch('pegasus_bridge.installation.sys.executable', 'C:\\用户\\Installed App\\python.exe'):
            result = client_config()
        config = result['mcpServers']['pegasus-editor']
        self.assertEqual(config['command'], 'C:\\用户\\Installed App\\python.exe')
        self.assertEqual(config['args'], ['-m', 'pegasus_bridge.server'])
        self.assertEqual(config['env'], {'PEGASUS_ALLOW_LOG_AUTH': '0'})

    def test_toml_roundtrip_with_spaces_unicode_and_quotes(self):
        path = '/tmp/a "quoted" 中文/python'
        with patch('pegasus_bridge.installation.sys.executable', path):
            config = tomllib.loads(client_config('codex'))['mcp_servers']['pegasus-editor']
        self.assertEqual(config['command'], path)
        self.assertEqual(config['env'], {'PEGASUS_ALLOW_LOG_AUTH': '0'})

    def test_unknown_client_rejected(self):
        with self.assertRaises(BridgeError):
            client_config('unknown')

    def test_server_version_does_not_start_stdio_loop(self):
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run([sys.executable, '-m', 'pegasus_bridge.server', '--version'],
                                    cwd=cwd, capture_output=True, encoding='utf-8', timeout=10, check=True)
        self.assertRegex(result.stdout.strip(), r'^pegasus-mcp \d+\.\d+\.\d+$')

    def test_doctor_real_protocol_from_unrelated_directory(self):
        report = doctor_mcp()
        self.assertTrue(report['initialize'])
        self.assertTrue(report['list_tools'])
        self.assertTrue(report['status_call'])
        self.assertEqual(report['transport'], 'stdio')
        self.assertEqual(report['tool_count'], 60)
        self.assertIn('pegasus_preview_start', report['tools'])
        self.assertIn('pegasus_save_draft', report['tools'])
        self.assertIn('pegasus_list_drafts', report['tools'])
        self.assertIsInstance(report['editable_install'], bool)

    def test_installer_version_matches_package(self):
        root = Path(__file__).resolve().parents[1]
        version = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['project']['version']
        self.assertIn("$packageVersion = '" + version + "'", (root / 'scripts/install.ps1').read_text())


if __name__ == '__main__':
    unittest.main()
