"""Real Redis atomic admission/overflow tests on a disposable network only."""

import asyncio
import json
import os

from app.provider_capacity import ImageCapacity


async def main():
    url = os.environ["POLARIS_IMAGE_CAPACITY_REDIS_URL"]
    limiters = [
        ImageCapacity(url, "capacity-test:atomic", 3000, 7200) for _ in range(3)
    ]
    client = limiters[0].client
    await client.delete("capacity-test:atomic")
    leases = await asyncio.gather(*(limiters[i % 3].acquire() for i in range(3000)))
    assert len(set(leases)) == 3000
    assert await client.zcard("capacity-test:atomic") == 3000
    overflow = asyncio.create_task(limiters[2].acquire())
    await asyncio.sleep(1)
    assert not overflow.done(), "3001st image must wait across worker replicas"
    await limiters[0].release(leases.pop())
    leases.append(await asyncio.wait_for(overflow, 10))
    assert await client.zcard("capacity-test:atomic") == 3000
    await asyncio.gather(
        *(limiters[i % 3].release(token) for i, token in enumerate(leases))
    )
    assert await client.zcard("capacity-test:atomic") == 0
    # Expired crash leases can be reclaimed without clearing live leases.
    await client.zadd("capacity-test:atomic", {"expired": 1})
    token = await limiters[1].acquire()
    assert await client.zcard("capacity-test:atomic") == 1
    await limiters[1].release(token)
    await client.delete("capacity-test:atomic")
    for limiter in limiters:
        await limiter.client.aclose()
        await limiter.client.connection_pool.disconnect()
    print(
        json.dumps(
            {
                "real_redis": True,
                "shared_clients": 3,
                "admitted": 3000,
                "overflow_waited": True,
                "expired_lease_reclaimed": True,
            }
        )
    )


asyncio.run(main())
