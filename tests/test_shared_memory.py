import unittest
import numpy as np

import multiprocessing as mp

mp.log_to_stderr()

from typing import NamedTuple

from memory import create_shared_memory_pair


class MyTuple(NamedTuple):
    a: int
    b: float
    c: str


class TestSharedMemory(unittest.TestCase):
    def test_None(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = None
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_None_tuple(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = (None, None)
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_None_list(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = [None, None]
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_None_dict(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = {None: None}
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_None_set(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = {None}
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_int_positive_small(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = 37
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_int_negative_small(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = -37
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_int_positive_big(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = 2147483640
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_int_negative_big(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = -2147483640
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_float1(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = 21474.83640
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_float2(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = -21474.83640
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_bool1(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = True
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_bool2(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = True
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_bytes(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = b"Hello World!"
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_bytes_empty(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = b""
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_str(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = "Hello World!"
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_str_empty(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = ""
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_str_long(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = """Zarathustra went down the mountain alone, no one meeting him. When he entered the forest, however, there suddenly stood before him an old man, who had left his holy cot to seek roots. And thus spake the old man to Zarathustra:
“No stranger to me is this wanderer: many years ago passed he by. Zarathustra he was called; but he hath altered.
Then thou carriedst thine ashes into the mountains: wilt thou now carry thy fire into the valleys? Fearest thou not the incendiary’s doom?
Yea, I recognise Zarathustra. Pure is his eye, and no loathing lurketh about his mouth. Goeth he not along like a dancer?
Altered is Zarathustra; a child hath Zarathustra become; an awakened one is Zarathustra: what wilt thou do in the land of the sleepers?
As in the sea hast thou lived in solitude, and it hath borne thee up. Alas, wilt thou now go ashore? Alas, wilt thou again drag thy body thyself?”
Zarathustra answered: “I love mankind.”
“Why,” said the saint, “did I go into the forest and the desert? Was it not because I loved men far too well?
Now I love God: men, I do not love. Man is a thing too imperfect for me. Love to man would be fatal to me.”
Zarathustra answered: “What spake I of love! I am bringing gifts unto men.”
“Give them nothing,” said the saint. “Take rather part of their load, and carry it along with them—that will be most agreeable unto them: if only it be agreeable unto thee!
If, however, thou wilt give unto them, give them no more than an alms, and let them also beg for it!”
“No,” replied Zarathustra, “I give no alms. I am not poor enough for that.”
The saint laughed at Zarathustra, and spake thus: “Then see to it that they accept thy treasures! They are distrustful of anchorites, and do not believe that we come with gifts.
The fall of our footsteps ringeth too hollow through their streets. And just as at night, when they are in bed and hear a man abroad long before sunrise, so they ask themselves concerning us: Where goeth the thief?
Go not to men, but stay in the forest! Go rather to the animals! Why not be like me—a bear amongst bears, a bird amongst birds?”
“And what doeth the saint in the forest?” asked Zarathustra.
The saint answered: “I make hymns and sing them; and in making hymns I laugh and weep and mumble: thus do I praise God.
With singing, weeping, laughing, and mumbling do I praise the God who is my God. But what dost thou bring us as a gift?”
When Zarathustra had heard these words, he bowed to the saint and said: “What should I have to give thee! Let me rather hurry hence lest I take aught away from thee!”—And thus they parted from one another, the old man and Zarathustra, laughing like schoolboys.
When Zarathustra was alone, however, he said to his heart: “Could it be possible! This old saint in the forest hath not yet heard of it, that God is dead!”"""
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_ndarray1(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = np.array([1, 2, 3, 4, 5], dtype=np.uint32)
        sender.put(data)
        item = receiver.get(timeout=2)
        np.testing.assert_array_equal(item, data, f"Expected {data}, got {item}")

    def test_ndarray2(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = np.array([[1.2, 2.3, 3.4], [-53.3, -3535, 1]], dtype=np.float32)
        sender.put(data)
        item = receiver.get(timeout=2)
        np.testing.assert_array_equal(item, data, f"Expected {data}, got {item}")

    def test_ndarray_big(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = np.random.rand(64, 30, 6, 84, 84).astype(np.float32)
        sender.put(data)
        item = receiver.get(timeout=2)
        np.testing.assert_array_equal(item, data, f"Expected {data}, got {item}")

    def test_ndtype1(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = np.dtype(np.float32)
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_ndtype2(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = np.dtype(np.bool_)
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_list(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = [1, 2, 3, 4, 5]
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_list_nested(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = [[1, 2, 3], [4, 5, 6]]
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_list_mixed_nested(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = [1, 2, [3, 4], 5]
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_list_nested_tuple(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = [1, 2, (3, 4), 5]
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_list_mixed(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = [1, "a", 3.4, 5, None, b"Hello World!"]
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_list_empty(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = []
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_list_empty_nested(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = [[], []]
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_list_empty_nested_tuple(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = [(), ()]
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_tuple(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = (1, 2, 3, 4, 5)
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_tuple_nested(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = ((1, 2, 3), (4, 5, 6))
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_tuple_mixed_nested(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = (1, 2, [3, 4], 5)
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_tuple_nested_list(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = (1, 2, [3, 4], 5)
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_tuple_mixed(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = (1, "a", 3.4, 5, None, b"Hello World!")
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_tuple_empty(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = ()
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_tuple_empty_nested(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = ((), ())
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_namedtuple(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = MyTuple(1, 2.3, "Hello World!")
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_dict(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = {1: "a", "b": 2, 3: 4}
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_dict_nested(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = {1: {2: 3, 4: 5}, "a": {"b": "c", "d": "e"}}
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_dict_mixed_nested(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = {1: {2: 3, 4: 5}, "a": ["b", "c", "d"]}
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_dict_mixed(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = {1: "a", "b": 2, 3: 4, "c": None, "d": b"Hello World!"}
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_dict_empty(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = {}
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_dict_empty_nested(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = {1: {}, "a": {}}
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_set(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = {1, 2, 3, 4, 5}
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_set_mixed(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = {1, 2, "a", None, 4}
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")

    def test_set_empty(self):
        sender, receiver = create_shared_memory_pair(capacity=1)
        data = set()
        sender.put(data)
        item = receiver.get(timeout=2)
        self.assertEqual(item, data, f"Expected {data}, got {item}")


if __name__ == "__main__":
    unittest.main()
