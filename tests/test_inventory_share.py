"""Only explicitly public inventory fields leave the home network."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec=importlib.util.spec_from_file_location('inventory_share',Path(__file__).resolve().parents[1]/'bambu_telegram_bot'/'inventory_share.py')
module=importlib.util.module_from_spec(spec)
with patch.dict(sys.modules,{'requests':Mock()}):
    spec.loader.exec_module(module)

class InventoryShareTest(unittest.TestCase):
    def test_allowlist_excludes_private_data(self):
        payload=module.public_inventory([{'id':1,'remaining_weight':200,'location':'My home','comment':'private',
            'filament':{'name':'Basic','material':'PLA','color_hex':'#aabbccff','price':20,'vendor':{'name':'Sunlu','comment':'private'}}}])
        self.assertEqual(payload,{'spools':[{'id':1,'brand':'Sunlu','name':'Basic','material':'PLA','colorHex':'AABBCC','colorName':'','remainingGrams':200.0}]})
    def test_empty_archived_missing_weight_and_color(self):
        self.assertEqual(module.public_inventory([]),{'spools':[]})
        self.assertEqual(module.public_inventory([{'id':1,'archived':True}]),{'spools':[]})
        for weight in (None,'bad',float('nan'),float('inf'),-1):
            spool=module.public_inventory([{'id':1,'remaining_weight':weight,'filament':{'vendor':None}}])['spools'][0]
            self.assertIsNone(spool['remainingGrams'])
            self.assertIsNone(spool['colorHex'])
    def test_posts_only_to_https_and_does_not_follow_redirects(self):
        http=Mock()
        http.get.return_value.json.return_value=[]
        http.post.return_value.status_code=200
        with patch.object(module,'requests',http):
            module.sync_once('http://spoolman','https://shared.example','test-key')
            http.post.assert_called_once_with('https://shared.example/api/sync',headers={'Authorization':'Bearer test-key'},json={'spools':[]},timeout=15,allow_redirects=False)
            for url in ('http://shared.example','https://user:pass@shared.example','https://shared.example?key=bad'):
                with self.assertRaises(ValueError):module.sync_once('http://spoolman',url,'test-key')
    def test_source_failure_never_overwrites_snapshot(self):
        http=Mock();http.get.return_value.raise_for_status.side_effect=RuntimeError('offline')
        with patch.object(module,'requests',http):
            with self.assertRaises(RuntimeError):module.sync_once('http://spoolman','https://shared.example','test-key')
            http.post.assert_not_called()
    def test_sharing_is_off_without_configuration(self):
        with patch.object(module.threading,'Thread') as thread:
            module.start_inventory_share('http://spoolman','','')
            thread.assert_not_called()

if __name__=='__main__':unittest.main()
