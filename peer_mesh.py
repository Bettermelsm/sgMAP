"""
peer_mesh.py — SGA 多节点互联模块
支持节点发现、Agent 同步、心跳检测
无配置时静默降级为单机模式
"""
import asyncio
import httpx
import logging
import time

log = logging.getLogger("peer_mesh")


class PeerMesh:
    def __init__(self):
        self.announced = False

    async def run(self, self_node_id: str):
        """主循环：announce + 定期心跳"""
        from main import SGA_PUBLIC_URL, SGA_SEED_NODES, db, add_event
        self_url = SGA_PUBLIC_URL or ""
        # Announce
        if self_url:
            for seed in SGA_SEED_NODES:
                try:
                    async with httpx.AsyncClient(timeout=5) as c:
                        await c.post(f"{seed}/api/peers/join", json={
                            "node_id": self_node_id,
                            "url": self_url,
                        })
                    log.info(f"Announced to seed: {seed}")
                except Exception as e:
                    log.warning(f"Announce failed for {seed}: {e}")
            self.announced = True
            add_event("system", None, f"节点已向 {len(SGA_SEED_NODES)} 个种子节点宣告", "info")

        # Periodic heartbeat to peers
        while True:
            await asyncio.sleep(30)
            await self._heartbeat_peers()

    async def _heartbeat_peers(self):
        from main import db
        peers = db().execute("SELECT * FROM peer_nodes").fetchall()
        now = time.time()
        for p in peers:
            try:
                async with httpx.AsyncClient(timeout=3) as c:
                    await c.get(f"{p['url']}/health")
                db().execute("UPDATE peer_nodes SET status='online', last_seen=? WHERE node_id=?",
                             (now, p["node_id"]))
            except Exception:
                db().execute("UPDATE peer_nodes SET status='offline' WHERE node_id=?",
                             (p["node_id"],))
        db().commit()
