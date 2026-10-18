"""Integration tests: authenticate and pass binary/text in both directions."""
import asyncio
import json
import unittest
from aiohttp import ClientSession, WSMsgType, web
from relay import make_app

GATEWAY = 'g' * 40
IPHONE = 'i' * 40

class RelayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runner = web.AppRunner(make_app(GATEWAY, IPHONE))
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await self.site.start()
        port = self.site._server.sockets[0].getsockname()[1]
        self.url = f'http://127.0.0.1:{port}'
        self.session = ClientSession()

    async def asyncTearDown(self):
        await self.session.close()
        await self.runner.cleanup()

    async def join(self, role, key):
        ws = await self.session.ws_connect(self.url + '/ws/' + role)
        await ws.send_json({'type': 'auth', 'token': key})
        return ws

    async def receive_type(self, ws, kind):
        while True:
            msg = await asyncio.wait_for(ws.receive(), 2)
            if msg.type == WSMsgType.TEXT:
                data = json.loads(msg.data)
                if data.get('type') == kind: return data
            else: self.fail('Socket unexpectedly closed before ' + kind)

    async def test_auth_required_and_invalid(self):
        ws = await self.join('gateway', 'wrong_key' * 6)
        msg = await asyncio.wait_for(ws.receive(), 2)
        self.assertIn(msg.type, (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED))
        await ws.close()
        r = await self.session.get(self.url + '/healthz')
        self.assertEqual((await r.json())['status'], 'ok')

    async def test_both_directions(self):
        gw = await self.join('gateway', GATEWAY)
        self.assertEqual((await self.receive_type(gw, 'ready'))['role'], 'gateway')
        await self.receive_type(gw, 'peer')
        ph = await self.join('iphone', IPHONE)
        self.assertEqual((await self.receive_type(ph, 'ready'))['role'], 'iphone')
        self.assertTrue((await self.receive_type(ph, 'peer'))['online'])
        self.assertTrue((await self.receive_type(gw, 'peer'))['online'])
        await ph.send_bytes(b'\x00\x01\xff' * 300)
        msg = await asyncio.wait_for(gw.receive(), 2)
        self.assertEqual(msg.type, WSMsgType.BINARY)
        self.assertEqual(msg.data, b'\x00\x01\xff' * 300)
        await gw.send_str('CALL ACTIVE')
        msg = await asyncio.wait_for(ph.receive(), 2)
        self.assertEqual(msg.type, WSMsgType.TEXT)
        self.assertEqual(msg.data, 'CALL ACTIVE')
        await ph.close()
        self.assertFalse((await self.receive_type(gw, 'peer'))['online'])
        await gw.close()

    async def test_unpaired_frames_are_dropped(self):
        gw = await self.join('gateway', GATEWAY)
        await self.receive_type(gw, 'ready')
        await self.receive_type(gw, 'peer')
        for _ in range(100):
            await gw.send_bytes(b'\x00' * 1000)
        ph = await self.join('iphone', IPHONE)
        await self.receive_type(ph, 'ready')
        await self.receive_type(ph, 'peer')
        await self.receive_type(gw, 'peer')
        await gw.send_bytes(b'fresh')
        msg = await asyncio.wait_for(ph.receive(), 2)
        self.assertEqual(msg.type, WSMsgType.BINARY)
        self.assertEqual(msg.data, b'fresh')
        await gw.close(); await ph.close()

    async def test_duplicate_gateway_refused(self):
        gw = await self.join('gateway', GATEWAY)
        await self.receive_type(gw, 'ready'); await self.receive_type(gw, 'peer')
        duplicate = await self.join('gateway', GATEWAY)
        msg = await asyncio.wait_for(duplicate.receive(), 2)
        self.assertIn(msg.type, (WSMsgType.CLOSE, WSMsgType.CLOSING, WSMsgType.CLOSED))
        await duplicate.close(); await gw.close()

if __name__ == '__main__':
    unittest.main(verbosity=2)
