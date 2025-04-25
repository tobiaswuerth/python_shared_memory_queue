from memory import create_shared_memory_pair

from performance_test_base import PerformanceTest


if __name__ == "__main__":

    def factory():
        sender, receiver = create_shared_memory_pair(None)
        return sender.put, receiver.get

    test = PerformanceTest("smq", factory)
    test.run()

    print("Test completed successfully.")
