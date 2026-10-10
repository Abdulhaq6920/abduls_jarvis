"""Create an admin in the DEMO data dir (JARVIS_DATA_DIR) for UI verification. Dev only."""
import asyncio

from jarvis_backend.auth.passwords import hash_password
from jarvis_backend.config import get_settings
from jarvis_backend.container import Container


async def main() -> None:
    c = await Container.create(get_settings())
    try:
        if not await c.auth.users.by_username("owner"):
            await c.auth.users.create("owner", await hash_password("correct horse battery staple"))
            print("seeded user 'owner'")
        else:
            print("user 'owner' already present")
    finally:
        await c.close()


asyncio.run(main())
