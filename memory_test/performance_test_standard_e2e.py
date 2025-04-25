import multiprocessing as mp

from performance_test_base import PerformanceTest


if __name__ == "__main__":

    def factory():
        q = mp.Queue()
        return q.put, q.get

    test = PerformanceTest("standard_e2e", factory)
    test.run_for_time(30)

    print("Test completed successfully.")
