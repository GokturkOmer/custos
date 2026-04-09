"""Simülatör CLI entry point.

Kullanım: python -m custos.simulator
"""

import asyncio

from custos.simulator.modbus_server import main

if __name__ == "__main__":
    asyncio.run(main())
