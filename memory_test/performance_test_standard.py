import multiprocessing as mp

from performance_test_base import PerformanceTest


if __name__ == "__main__":

    def factory():
        q = mp.Queue()
        return q.put, q.get

    test = PerformanceTest("standard", factory)
    test.run()

    print("Test completed successfully.")
