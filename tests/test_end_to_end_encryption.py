"""Cross-role AES-GCM relay test. No audio/control plaintext is delivered to the relay.
Only validates the transport and cryptography fixture, NOT the Android or iOS app.
"""
import asyncio
import hashlib
import hmac
import json
import unittest

from aiohttp import ClientSession, WSMsgType, web
from relay import make_app

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    AESGCM = None


def key(secret, salt, info):
    prk = hmac.new(salt, secret, hashlib.sha256).digest()
    return hmac.new(prk, info.encode() + b'\x01', hashlib.sha256).digest()


@unittest.skipIf(AESGCM is None, 'Install cryptography for optional E2EE transport fixture')
class EncryptedRoundTrip(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runner = web.AppRunner(make_app('g'*40, 'i'*40))
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await self.site.start()
        self.base = 'http://127.0.0.1:' + str(self.site._server.sockets[0].getsockname()[1])
        self.client = ClientSession()

    async def asyncTearDown(self):
        await self.client.close(); await self.runner.cleanup()

    async def test_encrypted_packets(self):
        async def join(role, password):
            socket = await self.client.ws_connect(self.base + '/ws/' + role)
            await socket.send_json({'type':'auth','token':password})
            return socket
        async def text(ws):
            msg = await asyncio.wait_for(ws.receive(), 2)
            self.assertEqual(msg.type, WSMsgType.TEXT)
            return json.loads(msg.data)
        async def binary(ws):
            msg=await asyncio.wait_for(ws.receive(),2)
            self.assertEqual(msg.type,WSMsgType.BINARY)
            return msg.data
        gw=await join('gateway','g'*40)
        await text(gw);await text(gw)
        ph=await join('iphone','i'*40)
        await text(ph);await text(ph);await text(gw)
        page = await self.client.get(self.base+'/')
        html=await page.text()
        self.assertEqual(page.status,200)
        self.assertIn('SIM Bridge',html)
        secret=bytes(range(24))
        salt=bytes(range(16))
        hello={'type':'hello','salt':'AAECAwQFBgcICQoLDA0ODw'}
        await ph.send_str(json.dumps(hello))
        self.assertEqual(json.loads((await gw.receive()).data)['type'],'hello')
        plaintext=b'\x01AUTH pairing-token-secret'
        txkey=key(secret,salt,'SIMBridge-Render-v1 iphone-to-gateway')
        aad=b'SBR1'+(0).to_bytes(8,'big')
        iv=bytes.fromhex('49324741')+(0).to_bytes(8,'big')
        ciphertext=aad+AESGCM(txkey).encrypt(iv,plaintext,aad)
        self.assertNotIn(plaintext,ciphertext)
        await ph.send_bytes(ciphertext)
        forwarded=await binary(gw)
        self.assertEqual(forwarded,ciphertext)
        self.assertEqual(AESGCM(txkey).decrypt(iv,forwarded[12:],forwarded[:12]),plaintext)
        response=b'\x01AUTH_OK'
        rkey=key(secret,salt,'SIMBridge-Render-v1 gateway-to-iphone')
        aad2=b'SBR1'+(0).to_bytes(8,'big')
        iv2=bytes.fromhex('47324950')+(0).to_bytes(8,'big')
        await gw.send_bytes(aad2+AESGCM(rkey).encrypt(iv2,response,aad2))
        wrapped=await binary(ph)
        self.assertEqual(AESGCM(rkey).decrypt(iv2,wrapped[12:],wrapped[:12]),response)
        await gw.close();await ph.close()

if __name__=='__main__': unittest.main()
