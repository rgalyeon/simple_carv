import json

from loguru import logger
from .account import Account
from aiohttp import ClientSession
from eth_account.messages import encode_defunct
import base64
from config import RPC, CARV_ABI
from utils.sleeping import sleep
from utils.helpers import retry


class Carv(Account):
    def __init__(self, wallet_info):
        super(Carv, self).__init__(wallet_info, chain="opbnb")
        self.headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            'Origin': 'https://protocol.carv.io',
            'Referer': 'https://protocol.carv.io/',
            'Content-Type': 'application/json',
            'User-Agent': wallet_info['user_agent']
        }

    async def get_utc(self):
        url = 'https://worldtimeapi.org/api/timezone/etc/UTC'
        async with ClientSession() as session:
            async with session.get(url, headers=self.headers, proxy=self.proxy) as response:
                time_ = await response.json()
        return time_

    async def login(self):
        logger.info(f"[{self.account_id}][{self.address}] Start login")
        unixtime = (await self.get_utc())['unixtime']
        message = (f'Hello! Please sign this message to confirm your ownership of the address. This action '
                   f'will not cost any gas fee. Here is a unique text: {int(unixtime) * 1000}')
        sign = self.w3.eth.account.sign_message(signable_message=encode_defunct(text=message),
                                                private_key=self.private_key)

        url = 'https://interface.carv.io/protocol/login'
        data = {
            "wallet_addr": self.address,
            "text": message,
            "signature": sign.signature.hex()
        }

        async with ClientSession() as session:
            async with session.post(url, headers=self.headers, proxy=self.proxy, data=json.dumps(data)) as response:
                res = await response.json()

        token = f"eoa:{res['data']['token']}"
        self.headers['Authorization'] = 'bearer ' + base64.b64encode(token.encode('utf-8')).decode('utf-8')

        logger.success(f"[{self.account_id}][{self.address}] Successfully login")
        await sleep(10, 15)

    async def check_mint_status(self, chain_id):
        url = f'https://interface.carv.io/airdrop/check_carv_status?chain_id={chain_id}'
        async with ClientSession() as session:
            async with session.get(url, headers=self.headers, proxy=self.proxy) as response:
                status = await response.json()
        return status['data']['status']

    async def check_in(self, chain_id):
        url = f'https://interface.carv.io/airdrop/mint/carv_soul'
        data = {"chain_id": str(chain_id)}
        async with ClientSession() as session:
            async with session.post(url, headers=self.headers, data=json.dumps(data), proxy=self.proxy) as response:
                res = await response.json()
        return res

    async def make_transaction(self, chain, data):
        await self.setup_w3(chain)

        contract_address = self.w3.to_checksum_address(data['contract'])
        mint_data = data['permit']
        signature = data['signature']
        account = self.w3.to_checksum_address(mint_data['account'])

        contract = self.get_contract(contract_address, CARV_ABI)
        mintData = (account, mint_data['amount'], mint_data['ymd'])

        tx_data = await self.get_tx_data()
        transaction = await contract.functions.mintSoul(mintData, signature).build_transaction(tx_data)
        signed_tx = await self.sign(transaction)
        tnx_hash = await self.send_raw_transaction(signed_tx)
        await self.wait_until_tx_finished(tnx_hash.hex())

    @retry
    async def mint_soul(self, chains, sleep_from, sleep_to):
        logger.info(f"[{self.account_id}][{self.address}] Start mint soul")
        await self.login()
        for chain in chains:
            chain_id = RPC[chain]['chain_id']
            mint_status = await self.check_mint_status(chain_id)
            if mint_status == 'not_started':
                response = await self.check_in(chain_id)
                data = response['data']
                if chain != "ronin":
                    await self.make_transaction(chain, data)
                logger.success(f"[{self.account_id}][{self.address}] Successfully minted on {chain.capitalize()}")
            else:
                logger.warning(f"[{self.account_id}][{self.address}] Already minted on {chain.capitalize()}")
            await sleep(sleep_from, sleep_to)
