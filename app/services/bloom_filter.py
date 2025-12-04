from pybloom_live import BloomFilter  # type: ignore[import-untyped]

bloom_filter = BloomFilter(capacity=1000000, error_rate=0.0001)
