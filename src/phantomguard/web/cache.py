"""Adapter for the anchor's existing pool cache; no attacker/planner changes.

Until Agent 2 supplies a public interface, warm workers namespace this private
cache by their verified content identity, clear it on any metadata/config change,
and freeze its arrays before reuse. Never import this module before numerical policy.
"""
from dataclasses import fields


def freeze_pools(cache):
    """Make shared prepared material read-only; discard oversized/extra cache entries.

    Conservative byte accounting counts array views too. Retain at most two pool
    variants and 64 MiB of array payload; recorded tuples separately stay bounded
    by approved files and the post-job worker RSS/recycling limit.
    """
    total=0
    for key,pool in list(cache.items()):
        for f in fields(pool):
            value=getattr(pool,f.name)
            if hasattr(value,'setflags'):
                value.setflags(write=False)
                total+=value.nbytes
            elif isinstance(value,(list,tuple)):
                for array in value:
                    if hasattr(array,'setflags'):
                        array.setflags(write=False)
                        total+=array.nbytes
                setattr(pool,f.name,tuple(value))
        if total>64*1024**2 or len(cache)>2:
            cache.clear()
            return {'retained_pool_bytes':0,'evicted':True}
    return {'retained_pool_bytes':total,'evicted':False}


def invalidate_input_caches():
    from phantomguard.io.replay import load_recorded_cycles
    from phantomguard.attack import pools
    from phantomguard.detect.autoencoder import _recording_digest
    load_recorded_cycles.cache_clear()
    pools._cache.clear()
    _recording_digest.cache_clear()
