# EPCopyFlow 2.0
# tests/test_engine_thread.py
# Testes unitários do EngineThread (issue #111, PR 1).
#
# Rodar:
#     python -m unittest tests.test_engine_thread -v

import asyncio
import concurrent.futures
import threading
import time
import unittest

from core.engine_thread import EngineThread


class EngineThreadTests(unittest.TestCase):
    def setUp(self):
        self.engine = EngineThread(name="TestEngine", ready_timeout=2.0)

    def tearDown(self):
        # Garantia de cleanup mesmo se um teste falhar antes do stop.
        try:
            self.engine.stop(timeout=2.0)
        except Exception:
            pass

    # ---------- start ----------

    def test_start_brings_loop_up_in_background_thread(self):
        self.engine.start()
        self.assertTrue(self.engine.is_running)
        self.assertTrue(self.engine.loop.is_running())
        # Thread do motor deve ser distinta da thread principal.
        self.assertNotEqual(threading.current_thread().ident, self._engine_thread_ident())

    def test_start_is_idempotent(self):
        self.engine.start()
        self.engine.start()  # não deve falhar
        self.assertTrue(self.engine.is_running)

    def test_loop_property_raises_before_start(self):
        with self.assertRaises(RuntimeError):
            _ = self.engine.loop

    # ---------- submit ----------

    def test_submit_returns_concurrent_future_with_result(self):
        self.engine.start()

        async def add(a, b):
            return a + b

        fut = self.engine.submit(add(2, 3))
        self.assertIsInstance(fut, concurrent.futures.Future)
        self.assertEqual(fut.result(timeout=2.0), 5)

    def test_submit_runs_in_engine_thread_not_caller(self):
        self.engine.start()
        engine_ident = self._engine_thread_ident()

        async def who_am_i():
            return threading.current_thread().ident

        ident = self.engine.submit(who_am_i()).result(timeout=2.0)
        self.assertEqual(ident, engine_ident)
        self.assertNotEqual(ident, threading.current_thread().ident)

    def test_submit_rejects_non_coroutine(self):
        self.engine.start()
        with self.assertRaises(TypeError):
            self.engine.submit(lambda: 42)

    def test_submit_before_start_raises(self):
        async def noop():
            return None

        coro = noop()
        with self.assertRaises(RuntimeError):
            self.engine.submit(coro)

    # ---------- robustez: exceção em coro não derruba o loop ----------

    def test_exception_in_coro_does_not_kill_loop(self):
        self.engine.start()

        async def boom():
            raise ValueError("explode")

        bad = self.engine.submit(boom())
        with self.assertRaises(ValueError):
            bad.result(timeout=2.0)

        # Loop deve continuar saudável depois da exceção.
        self.assertTrue(self.engine.is_running)

        async def healthy():
            return "ok"

        good = self.engine.submit(healthy())
        self.assertEqual(good.result(timeout=2.0), "ok")

    def test_unobserved_exception_does_not_kill_loop(self):
        """Exceção em task fire-and-forget também não pode derrubar o loop."""
        self.engine.start()

        async def schedule_unobserved():
            asyncio.get_running_loop().create_task(self._raise_async())

        self.engine.submit(schedule_unobserved()).result(timeout=2.0)
        # Espera curta para o handler de exceção rodar.
        time.sleep(0.1)
        self.assertTrue(self.engine.is_running)

        async def ping():
            return "pong"

        self.assertEqual(self.engine.submit(ping()).result(timeout=2.0), "pong")

    @staticmethod
    async def _raise_async():
        raise RuntimeError("unobserved")

    # ---------- stop ----------

    def test_stop_returns_true_within_timeout(self):
        self.engine.start()
        self.assertTrue(self.engine.stop(timeout=2.0))
        self.assertFalse(self.engine.is_running)

    def test_stop_cancels_pending_tasks(self):
        self.engine.start()

        # Agenda uma coroutine que dorme bem mais do que o timeout do stop.
        async def long_sleep():
            await asyncio.sleep(60)

        self.engine.submit(long_sleep())

        t0 = time.monotonic()
        finished = self.engine.stop(timeout=3.0)
        elapsed = time.monotonic() - t0

        self.assertTrue(finished)
        # Stop deve ter sido bem mais rápido que os 60s da coro.
        self.assertLess(elapsed, 3.0)

    def test_stop_is_idempotent(self):
        self.engine.start()
        self.assertTrue(self.engine.stop(timeout=2.0))
        # Segundo stop não deve travar nem lançar.
        self.assertTrue(self.engine.stop(timeout=1.0))

    def test_cannot_restart_after_stop(self):
        self.engine.start()
        self.engine.stop(timeout=2.0)
        with self.assertRaises(RuntimeError):
            self.engine.start()

    # ---------- helpers ----------

    def _engine_thread_ident(self):
        # Acessa atributo privado só para teste — única forma de provar
        # que a coro rodou na thread do motor.
        thr = self.engine._thread
        self.assertIsNotNone(thr)
        return thr.ident


if __name__ == "__main__":
    unittest.main()
