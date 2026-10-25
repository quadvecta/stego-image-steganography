import os
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np

import stego_engine as se


def synth(h=120, w=160, seed=1):
    rng = np.random.default_rng(seed)
    base = np.linspace(0, 255, w, dtype=np.uint8)[None, :, None].repeat(h, 0).repeat(3, 2)
    return np.clip(base.astype(int) + rng.integers(-20, 20, (h, w, 3)), 0, 255).astype(np.uint8)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.img = synth()

    # 1-3 round trips
    def test_short(self):
        r = se.stego_encode(self.img, "hello")
        self.assertEqual(se.stego_decode(r.img_out, r.token), "hello")

    def test_long(self):
        msg = "The quick brown fox. " * 60
        r = se.stego_encode(self.img, msg)
        self.assertEqual(se.stego_decode(r.img_out, r.token), msg)

    def test_unicode_and_empty(self):
        for msg in ("héllo ✓ 日本語 🙂", ""):
            r = se.stego_encode(self.img, msg)
            self.assertEqual(se.stego_decode(r.img_out, r.token), msg)

    # 4 capacity
    def test_capacity_exact_and_exceeded(self):
        small = synth(40, 40)
        m = se.max_plaintext_bytes(small.shape)
        self.assertGreater(m, 0)
        r = se.stego_encode(small, "a" * m)                       # exactly fits
        self.assertEqual(se.stego_decode(r.img_out, r.token), "a" * m)
        self.assertLessEqual(r.n_pixels_used, 40 * 40)
        with self.assertRaises(se.StegoError):
            se.stego_encode(small, "a" * (m + 1))

    def test_fernet_len_model(self):
        from cryptography.fernet import Fernet
        f = Fernet(Fernet.generate_key())
        for n in (0, 1, 15, 16, 17, 100, 1000):
            self.assertEqual(len(f.encrypt(b"x" * n)), se.fernet_len(n))

    # 5 invalid input
    def test_invalid_images(self):
        for bad in (None, np.zeros((10, 10), np.uint8), np.zeros((10, 10, 4), np.uint8),
                    np.zeros((10, 10, 3), np.float32), "nope"):
            with self.assertRaises(se.StegoError):
                se.stego_encode(bad, "x")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "bad.png")
            with open(p, "wb") as f:
                f.write(b"not an image")
            with self.assertRaises(se.StegoError):
                se.read_image(p)
            with self.assertRaises(se.StegoError):
                se.read_image(os.path.join(d, "missing.png"))

    # 6 wrong / malformed token
    def test_wrong_token(self):
        r = se.stego_encode(self.img, "secret")
        with self.assertRaises(se.StegoError):
            se.stego_decode(r.img_out, se.new_token())
        for bad in ("", "!!!", "QUJD", "A" * 24):
            with self.assertRaises(se.StegoError):
                se.stego_decode(r.img_out, bad)

    def test_unmodified_cover_is_not_a_stego_image(self):
        with self.assertRaises(se.StegoError):
            se.stego_decode(self.img, se.new_token())

    # 7 corruption
    def test_corrupted_image(self):
        r = se.stego_encode(self.img, "secret message" * 5)
        # Flip LSBs at payload positions -> authentication must fail
        bad = r.img_out.copy()
        flat = bad.reshape(-1, 3)
        pos = r.payload_positions[: len(r.payload_positions) // 2]
        flat[pos, 0] ^= 0x03
        with self.assertRaises(se.StegoError):
            se.stego_decode(bad, r.token)
        # Corrupt header
        bad2 = r.img_out.copy()
        bad2.reshape(-1, 3)[r.header_positions[:8], 0] ^= 0x03
        with self.assertRaises(se.StegoError):
            se.stego_decode(bad2, r.token)
        # Noise everywhere
        noisy = np.clip(r.img_out.astype(int) + np.random.default_rng(0).integers(-3, 4, r.img_out.shape),
                        0, 255).astype(np.uint8)
        with self.assertRaises(se.StegoError):
            se.stego_decode(noisy, r.token)

    # 8 encoder/decoder position agreement
    def test_positions_agree_with_decoder(self):
        r = se.stego_encode(self.img, "agree")
        _, scat, _ = se._derive(se.parse_token(r.token))
        order = se.scatter_order(self.img.shape[0] * self.img.shape[1], scat)
        np.testing.assert_array_equal(order[: r.n_pixels_used], r.positions)
        self.assertEqual(len(set(r.positions.tolist())), r.n_pixels_used)      # unique
        # only recorded pixels' blue channel may differ, and only in 2 LSBs
        diff = (r.img_out.astype(int) != self.img.astype(int))
        self.assertFalse(diff[..., 1:].any())
        changed = np.flatnonzero(diff[..., 0].reshape(-1))
        self.assertTrue(set(changed.tolist()) <= set(r.positions.tolist()))
        self.assertLessEqual(np.abs(r.img_out.astype(int) - self.img.astype(int)).max(), 3)

    def test_scatter_prefix_stable_and_key_dependent(self):
        k1, k2 = b"\x01" * 32, b"\x02" * 32
        a = se.scatter_order(5000, k1)
        np.testing.assert_array_equal(a, se.scatter_order(5000, k1))
        self.assertFalse(np.array_equal(a[:100], se.scatter_order(5000, k2)[:100]))
        self.assertEqual(sorted(a.tolist()), list(range(5000)))

    # 9, 10 heatmap
    def test_heatmap_uses_exact_positions(self):
        r = se.stego_encode(self.img, "heat" * 20)
        counts = se.position_counts(r.shape, r.positions)
        self.assertEqual(counts.shape, self.img.shape[:2])
        self.assertEqual(int(counts.sum()), r.n_pixels_used)
        np.testing.assert_array_equal(np.flatnonzero(counts.reshape(-1)), np.sort(r.positions))
        # header/payload split is consistent
        self.assertEqual(len(r.header_positions) + len(r.payload_positions), r.n_pixels_used)
        self.assertEqual(r.n_header_pixels, se.HEADER_BYTES * 4)

    def test_aggregation_preserves_mass_and_alignment(self):
        r = se.stego_encode(self.img, "x" * 200)
        counts = se.position_counts(r.shape, r.positions)
        for b in (1, 7, 16):
            binned = se.bin_density(counts, b)
            self.assertEqual(binned.sum(), counts.sum())
            self.assertEqual(binned.shape, (-(-120 // b), -(-160 // b)))
        # a single known position lands in the expected cell
        c = se.position_counts((20, 30, 3), np.array([5 * 30 + 17]))
        bd = se.bin_density(c, 10)
        self.assertEqual(bd[0, 1], 1)
        self.assertEqual(bd.sum(), 1)

    def test_heatmap_rejects_missing_or_bad_positions(self):
        with self.assertRaises(se.StegoError):
            se.position_counts((10, 10, 3), np.array([], dtype=np.int64))
        with self.assertRaises(se.StegoError):
            se.position_counts((10, 10, 3), np.array([100]))

    # 14 heatmap does not modify images
    def test_heatmap_render_does_not_mutate(self):
        r = se.stego_encode(self.img, "immut" * 10)
        out_before, cover_before = r.img_out.copy(), r.cover.copy()
        counts = se.position_counts(r.shape, r.positions)
        for ov in (True, False):
            img, lo, hi = se.render_heatmap(r.cover, se.bin_density(counts, se.auto_bin(r.shape)), se.auto_bin(r.shape), overlay=ov)
            self.assertEqual(img.ndim, 3)
        np.testing.assert_array_equal(r.img_out, out_before)
        np.testing.assert_array_equal(r.cover, cover_before)
        self.assertEqual(se.stego_decode(r.img_out, r.token), "immut" * 10)

    # 11, 13 lossless save/reload, dimensions
    def test_png_and_bmp_roundtrip_and_dimensions(self):
        for ext in (".png", ".bmp"):
            with tempfile.TemporaryDirectory() as d:
                r = se.stego_encode(self.img, "persist ✓")
                self.assertEqual(r.img_out.shape, self.img.shape)
                p = os.path.join(d, "ü_out" + ext)                       # non-ASCII path
                se.save_and_verify(r, p)
                self.assertTrue(r.verified)
                self.assertEqual(r.output_path, p)
                back = se.read_image(p)
                self.assertEqual(back.shape, self.img.shape)
                self.assertEqual(se.stego_decode(back, r.token), "persist ✓")

    # 12 failed write
    def test_failed_write_is_not_success(self):
        r = se.stego_encode(self.img, "x")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(se.StegoError):
                se.save_and_verify(r, os.path.join(d, "no_such_dir", "o.png"))
            with self.assertRaises(se.StegoError):
                se.save_and_verify(r, os.path.join(d, "o.xyz"))
        self.assertFalse(r.verified)
        self.assertIsNone(r.output_path)
        with mock.patch("cv2.imencode", return_value=(False, None)):
            with self.assertRaises(se.StegoError):
                se.save_and_verify(r, os.path.join(tempfile.gettempdir(), "never.png"))
        self.assertFalse(r.verified)

    def test_jpeg_not_reported_as_success_if_unrecoverable(self):
        r = se.stego_encode(synth(200, 200), "jpeg test")
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "o.jpg")
            try:
                se.save_and_verify(r, p)
                self.assertTrue(r.verified)                   # only if it really decodes
                self.assertEqual(se.stego_decode(se.read_image(p), r.token), "jpeg test")
            except se.SaveVerificationError:
                self.assertFalse(r.verified)
                self.assertFalse(os.path.exists(p))           # failed file removed

    # analysis
    def test_compare_metrics(self):
        r = se.stego_encode(self.img, "m" * 50)
        m = se.compare(r.cover, r.img_out)
        self.assertGreater(m["psnr"], 40)
        self.assertEqual(se.compare(self.img, self.img)["psnr"], float("inf"))
        with self.assertRaises(se.StegoError):
            se.compare(self.img, self.img[:10])
        self.assertEqual(se.diff_image(r.cover, r.img_out).shape[2], 3)
        big = synth(900, 1200)
        rb = se.stego_encode(big, "big" * 50)
        d = se.diff_image(rb.cover, rb.img_out, 300)
        self.assertEqual(d.shape, (225, 300, 3))
        self.assertGreater(int(d.max()), 0)
        self.assertEqual(se.histogram_plot(r.cover, r.img_out).shape, (200, 520, 3))

    def test_metric_definitions(self):
        r = se.stego_encode(self.img, "m" * 50)
        self.assertAlmostEqual(r.pixel_fraction, r.n_pixels_used / (120 * 160))
        self.assertAlmostEqual(r.channel_bit_fraction, r.pixel_fraction * 2 / 24)

    def test_v0_token_message(self):
        import base64
        # The legacy 16-byte salt token is v0.x; the parser already labels it correctly.
        with self.assertRaisesRegex(se.StegoError, r"v0\.x"):
            se.parse_token(base64.urlsafe_b64encode(b"\x00" * 16).decode())


if __name__ == "__main__":
    unittest.main(verbosity=2)
