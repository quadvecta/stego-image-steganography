"""Engine-only safety, geometry and uploaded-original compatibility regressions."""

import ast
import base64
import importlib.util
import os
from pathlib import Path
import stat
import struct
import sys
import tempfile
import unittest
from unittest import mock

import cv2
import numpy as np

import stego_engine as se


def image(h=48, w=67):
    return np.random.default_rng(730).integers(0, 256, (h, w, 3), dtype=np.uint8)


def metadata(res):
    return res.output_path, res.output_format, res.output_bytes, res.verified


class SaveRegressionTests(unittest.TestCase):
    message = "Intended message: \u65e5\u672c\u8a9e \U0001f642"

    def failure_matrix(self, patcher, error=se.SaveVerificationError, existing=(False, True)):
        for ext in (".png", ".bmp"):
            for exists in existing:
                for verified in (False, True):
                    with self.subTest(ext=ext, exists=exists, verified=verified):
                        with tempfile.TemporaryDirectory() as directory:
                            root = Path(directory)
                            target = root / ("\u00fc_target" + ext)
                            (root / ".stego-unowned.png").write_bytes(b"unrelated file")
                            res = se.stego_encode(image(), self.message)
                            if exists:
                                target.write_bytes(b"preexisting destination")
                            if verified:
                                se.save_and_verify(res, str(target if exists else root / "prior.png"))
                            if exists and os.name == "posix":
                                target.chmod(0o640)
                            before = {p.name: p.read_bytes() for p in root.iterdir()}
                            modes = {p.name: stat.S_IMODE(p.stat().st_mode) for p in root.iterdir()}
                            previous = metadata(res)
                            encoded, cover = res.img_out.copy(), res.cover.copy()
                            with patcher(res, target):
                                with self.assertRaises(error):
                                    se.save_and_verify(res, str(target))
                            self.assertEqual(metadata(res), previous)
                            self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, before)
                            self.assertEqual({p.name: stat.S_IMODE(p.stat().st_mode)
                                              for p in root.iterdir()}, modes)
                            np.testing.assert_array_equal(res.img_out, encoded)
                            np.testing.assert_array_equal(res.cover, cover)
                            if verified:
                                self.assertEqual(se.stego_decode(se.read_image(res.output_path),
                                                                 res.token), self.message)

    def test_encoder_failure_and_exception(self):
        for effect in ({"return_value": (False, None)}, {"side_effect": cv2.error("encoder")}):
            with self.subTest(effect=effect):
                self.failure_matrix(lambda r, p: mock.patch.object(se.cv2, "imencode", **effect))

    def test_invalid_original_decode_preserves_destination(self):
        self.failure_matrix(lambda r, p: mock.patch.object(
            se, "stego_decode", side_effect=se.StegoError("invalid original")))

    def test_temporary_creation_failure(self):
        self.failure_matrix(lambda r, p: mock.patch.object(
            se.tempfile, "NamedTemporaryFile", side_effect=PermissionError("create")))

    def test_partial_and_short_writes(self):
        real_temp = se.tempfile.NamedTemporaryFile
        for short in (False, True):
            def partial(*args, **kwargs):
                f = real_temp(*args, **kwargs)
                write = f.write

                def interrupted(data):
                    written = write(data[:17])
                    if short:
                        return written
                    raise OSError("partial write")

                f.write = interrupted
                return f

            with self.subTest(short=short):
                self.failure_matrix(lambda r, p: mock.patch.object(
                    se.tempfile, "NamedTemporaryFile", side_effect=partial))

    def test_flush_failure(self):
        real_temp = se.tempfile.NamedTemporaryFile

        def fail_flush(*args, **kwargs):
            f = real_temp(*args, **kwargs)
            f.flush = mock.Mock(side_effect=OSError("flush"))
            return f

        self.failure_matrix(lambda r, p: mock.patch.object(
            se.tempfile, "NamedTemporaryFile", side_effect=fail_flush))

    def test_fsync_failure(self):
        self.failure_matrix(lambda r, p: mock.patch.object(se.os, "fsync", side_effect=OSError("fsync")))

    def test_output_size_failure(self):
        for effect in ({"return_value": 0}, {"side_effect": OSError("stat")}):
            with self.subTest(effect=effect):
                self.failure_matrix(lambda r, p: mock.patch.object(se.os.path, "getsize", **effect))

    def test_reopen_failure(self):
        self.failure_matrix(lambda r, p: mock.patch.object(
            se, "read_image", side_effect=se.StegoError("reopen")))

    def test_dimensions_mismatch(self):
        self.failure_matrix(lambda r, p: mock.patch.object(se, "read_image", return_value=r.img_out[:-1]))

    def test_lossless_pixels_mismatch(self):
        def changed_pixels(res, path):
            back = res.img_out.copy()
            back[0, 0, 1] ^= 1  # Green change leaves the payload valid but is not lossless.
            self.assertEqual(se.stego_decode(back, res.token), self.message)
            return mock.patch.object(se, "read_image", return_value=back)

        self.failure_matrix(changed_pixels)

    def test_verification_decode_failure(self):
        self.failure_matrix(lambda r, p: mock.patch.object(
            se, "stego_decode", side_effect=[self.message, se.StegoError("verification")]))

    def test_successful_decode_of_wrong_message_is_rejected(self):
        self.failure_matrix(lambda r, p: mock.patch.object(
            se, "stego_decode", side_effect=[self.message, "different valid plaintext"]))

    def test_commit_failure(self):
        self.failure_matrix(lambda r, p: mock.patch.object(se.os, "replace", side_effect=OSError("commit")))

    def test_chmod_failure_preserves_existing_destination(self):
        self.failure_matrix(lambda r, p: mock.patch.object(
            se.os, "chmod", side_effect=OSError("chmod unavailable")), existing=(True,))

    def test_access_denied_preserves_existing_destination(self):
        self.failure_matrix(lambda r, p: mock.patch.object(
            se.os, "access", return_value=False), existing=(True,))

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits required")
    def test_silently_unsupported_mode_change_is_rejected(self):
        self.failure_matrix(lambda r, p: mock.patch.object(
            se.os, "chmod", return_value=None), existing=(True,))

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits required")
    def test_existing_posix_mode_0640_is_retained_without_old_times(self):
        for ext in (".png", ".bmp"):
            with self.subTest(ext=ext), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / ("chosen" + ext)
                target.write_bytes(b"replace this chosen destination")
                target.chmod(0o640)
                os.utime(target, (1, 1))
                res = se.stego_encode(image(), self.message)
                with mock.patch.object(se.os, "chmod", wraps=os.chmod) as chmod:
                    se.save_and_verify(res, str(target))
                chmod.assert_called_once()
                staged, mode = chmod.call_args.args
                self.assertNotEqual(Path(staged), target)
                self.assertEqual(Path(staged).parent, target.parent)
                self.assertEqual(mode, 0o640)
                self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o640)
                self.assertNotEqual(target.stat().st_mtime, 1)
                self.assertEqual(se.stego_decode(se.read_image(str(target)), res.token), self.message)
                self.assertTrue(res.verified)
                self.assertEqual(list(Path(directory).iterdir()), [target])

    @unittest.skipUnless(os.name == "posix", "POSIX permission bits required")
    def test_read_only_posix_mode_0444_is_unchanged_and_rejected(self):
        for ext in (".png", ".bmp"):
            for access_override in (False, True):
                with self.subTest(ext=ext, privileged=access_override):
                    with tempfile.TemporaryDirectory() as directory:
                        target = Path(directory) / ("read_only" + ext)
                        res = se.stego_encode(image(), self.message)
                        se.save_and_verify(res, str(target))
                        target.chmod(0o444)
                        before, previous = target.read_bytes(), metadata(res)
                        access = {"return_value": True} if access_override else {"wraps": os.access}
                        with (mock.patch.object(se.os, "access", **access),
                              mock.patch.object(se.os, "replace", wraps=os.replace) as replace):
                            with self.assertRaisesRegex(se.SaveVerificationError, "read-only|not writable"):
                                se.save_and_verify(res, str(target))
                            replace.assert_not_called()
                        self.assertEqual(target.read_bytes(), before)
                        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o444)
                        self.assertEqual(metadata(res), previous)
                        self.assertEqual(list(Path(directory).iterdir()), [target])

    def test_interruption_also_cleans_owned_temporary(self):
        self.failure_matrix(lambda r, p: mock.patch.object(
            se.os, "fsync", side_effect=KeyboardInterrupt), error=KeyboardInterrupt)

    def test_lossy_failure_preserves_existing_destination(self):
        res = se.stego_encode(image(), self.message)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "existing.jpg"
            path.write_bytes(b"must survive")
            with self.assertRaises(se.SaveVerificationError):
                se.save_and_verify(res, str(path))
            self.assertEqual(path.read_bytes(), b"must survive")
            self.assertEqual(list(Path(directory).iterdir()), [path])
            self.assertEqual(metadata(res), (None, None, None, False))

    def test_unicode_lossless_commit_order_and_unique_staging(self):
        real_replace, real_fsync = se.os.replace, se.os.fsync
        real_read, real_decode, real_encode = se.read_image, se.stego_decode, se.cv2.imencode
        for ext in (".PNG", ".bmp"):
            with self.subTest(ext=ext), tempfile.TemporaryDirectory() as directory:
                target = Path(directory) / ("\u65e5\u672c\u8a9e_\u00fc" + ext)
                res = se.stego_encode(image(), self.message)
                paths = []
                for attempt in range(2):
                    steps, prior = [], metadata(res)
                    old_bytes = target.read_bytes() if target.exists() else None

                    def decode(img, token):
                        steps.append("decode")
                        if len(steps) == 1:
                            self.assertIs(img, res.img_out)
                        return real_decode(img, token)

                    def encode(*args):
                        steps.append("encode")
                        return real_encode(*args)

                    def fsync(fd):
                        steps.append("fsync")
                        self.assertGreater(os.fstat(fd).st_size, 0)
                        return real_fsync(fd)

                    def read(path):
                        steps.append("reopen")
                        self.assertNotEqual(Path(path), target)
                        return real_read(path)

                    def replace(source, destination):
                        steps.append("replace")
                        self.assertEqual(Path(source).parent, target.parent)
                        self.assertEqual(Path(destination), target)
                        self.assertEqual(metadata(res), prior)
                        self.assertEqual(target.read_bytes() if target.exists() else None, old_bytes)
                        self.assertEqual(real_decode(real_read(source), res.token), self.message)
                        paths.append(source)
                        return real_replace(source, destination)

                    with (mock.patch.object(se, "stego_decode", side_effect=decode),
                          mock.patch.object(se.cv2, "imencode", side_effect=encode),
                          mock.patch.object(se.os, "fsync", side_effect=fsync),
                          mock.patch.object(se, "read_image", side_effect=read),
                          mock.patch.object(se.os, "replace", side_effect=replace)):
                        self.assertIs(se.save_and_verify(res, str(target)), res)
                    self.assertEqual(steps, ["decode", "encode", "fsync", "reopen", "decode", "replace"])
                    self.assertEqual(metadata(res), (str(target), ext[1:].upper(), target.stat().st_size, True))
                    np.testing.assert_array_equal(real_read(str(target)), res.img_out)
                    self.assertEqual(list(Path(directory).iterdir()), [target])
                self.assertNotEqual(*paths)


class HeatmapRegressionTests(unittest.TestCase):
    def test_density_matches_pixel_counts_at_every_edge_and_with_duplicates(self):
        rng = np.random.default_rng(19)
        for h, w in ((1, 1), (2, 3), (11, 17), (29, 31)):
            positions = np.concatenate((np.arange(h * w), rng.integers(0, h * w, 200),
                                        [0, w - 1, (h - 1) * w, h * w - 1]))
            counts = se.position_counts((h, w, 3), positions)
            self.assertEqual(counts.dtype, np.int32)
            for b in (1, 2, 3, 7, 10, 64, np.int64(4)):
                with self.subTest(h=h, w=w, bin=b):
                    expected = np.array([[counts[y:y+b, x:x+b].sum()
                                          for x in range(0, w, b)] for y in range(0, h, b)],
                                        dtype=np.float64)
                    np.testing.assert_array_equal(se.bin_density(counts, b), expected)
                    direct = se.position_density((h, w, 3), positions, b)
                    np.testing.assert_array_equal(direct, expected)
                    self.assertEqual(direct.dtype, np.float64)
                    self.assertEqual(direct.sum(), len(positions))

    def test_header_and_payload_densities_add_to_all_positions(self):
        res = se.stego_encode(image(), "header and payload")
        for b in (1, 7, 64):
            all_hits = se.position_density(res.shape, res.positions, b)
            header = se.position_density(res.shape, res.header_positions, b)
            payload = se.position_density(res.shape, res.payload_positions, b)
            np.testing.assert_array_equal(all_hits, header + payload)
            self.assertEqual(header.sum(), se.HEADER_BYTES * se.PIXELS_PER_BYTE)
            self.assertEqual(payload.sum(), res.ciphertext_bytes * se.PIXELS_PER_BYTE)

    def test_direct_density_does_not_call_full_grid_apis(self):
        with (mock.patch.object(se, "position_counts", side_effect=AssertionError("full grid")),
              mock.patch.object(se, "bin_density", side_effect=AssertionError("full grid"))):
            result = se.position_density((2160, 3840, 3), np.array([0, 8294399, 8294399]), 61)
        self.assertEqual(result.shape, (36, 63))
        self.assertEqual(result[0, 0], 1)
        self.assertEqual(result[-1, -1], 2)

    def test_empty_counts_have_well_defined_empty_grids(self):
        for shape in ((0, 0), (0, 7), (5, 0)):
            for b in (1, 3, 10):
                result = se.bin_density(np.empty(shape, dtype=np.int32), b)
                self.assertEqual(result.shape, tuple(-(-n // b) for n in shape))
                self.assertEqual(result.dtype, np.float64)
                self.assertEqual(result.size, 0)

    def test_missing_positions_remain_rejected_by_both_apis(self):
        for positions in (None, [], np.array([], dtype=np.int64), np.empty((0, 2))):
            for function, args in ((se.position_counts, ()), (se.position_density, (3,))):
                with self.subTest(positions=positions, function=function.__name__):
                    with self.assertRaisesRegex(se.StegoError, "No embedding positions"):
                        function((10, 10, 3), positions, *args)

    def test_invalid_positions_and_shapes(self):
        bad_positions = ([-1], [100], [1.5], [True], [[1]], [[1], [1, 2]],
                         ["1"], np.array([1], dtype=object), 1, [np.nan], [2**64 - 1])
        for positions in bad_positions:
            for function, args in ((se.position_counts, ()), (se.position_density, (3,))):
                with self.subTest(positions=positions, function=function.__name__):
                    with self.assertRaises(se.StegoError):
                        function((10, 10, 3), positions, *args)
        for shape in (None, (), (10,), (0, 10), (10, -1), (1.5, 3), (True, 3), "bad"):
            for function, args in ((se.position_counts, ()), (se.position_density, (3,))):
                with self.subTest(shape=shape, function=function.__name__):
                    with self.assertRaises(se.StegoError):
                        function(shape, np.array([0]), *args)

    def test_invalid_bins_rejected_consistently(self):
        for b in (0, -1, 1.5, True, np.bool_(True), np.nan, np.inf, None, "2"):
            with self.subTest(bin=b):
                with self.assertRaises(se.StegoError):
                    se.bin_density(np.zeros((2, 3)), b)
                with self.assertRaises(se.StegoError):
                    se.position_density((2, 3), np.array([0]), b)
                with self.assertRaises(se.StegoError):
                    se.render_heatmap(image(2, 3), np.zeros((2, 3)), b)

    def test_invalid_density_and_render_parameters(self):
        for grid in (None, [[1]], np.array([1]), np.zeros((2, 2, 1)), np.array([["a"]]),
                     np.array([[1j]]), np.array([[-1]]), np.array([[np.nan]]), np.array([[np.inf]])):
            with self.subTest(grid=grid):
                with self.assertRaises(se.StegoError):
                    se.bin_density(grid, 2)
                with self.assertRaises(se.StegoError):
                    se.render_heatmap(image(2, 2), grid, 2)
        for grid in (np.empty((0, 0)), np.zeros((2, 1)), np.zeros((1, 2))):
            with self.assertRaises(se.StegoError):
                se.render_heatmap(image(2, 2), grid, 2)
        for alpha in (-0.1, 1.1, np.nan, np.inf, None, "0.6"):
            with self.assertRaises(se.StegoError):
                se.render_heatmap(image(2, 2), np.ones((1, 1)), 2, alpha=alpha)
        for size in (0, -1, 3.5, True, None):
            with self.assertRaises(se.StegoError):
                se.render_heatmap(image(2, 2), np.ones((1, 1)), 2, max_dim=size)

    def test_constant_positive_grid_is_not_rendered_as_empty(self):
        cover = image(5, 7)
        for value, level in ((0, 0), (1, 255), (17, 255)):
            with self.subTest(value=value):
                rendered, lo, hi = se.render_heatmap(cover, np.full((2, 3), value), 3, overlay=False)
                expected = cv2.applyColorMap(np.full((5, 7), level, np.uint8), cv2.COLORMAP_INFERNO)
                np.testing.assert_array_equal(rendered, expected)
                self.assertEqual((lo, hi), (value, value))

    def test_clipped_edge_alignment_matches_cropped_expansion_oracle(self):
        for h, w, b in ((11, 11, 10), (13, 17, 5), (7, 23, 6), (23, 7, 6), (1, 19, 7)):
            grid = np.arange(-(-h // b) * -(-w // b), dtype=float).reshape(-(-h // b), -(-w // b))
            u8 = (grid / grid.max() * 255).astype(np.uint8)
            expanded = np.repeat(np.repeat(u8, b, axis=0), b, axis=1)[:h, :w]
            cover = image(h, w)
            for max_dim in (1, 8, 560):
                for overlay in (False, True):
                    with self.subTest(h=h, w=w, bin=b, max_dim=max_dim, overlay=overlay):
                        rendered, lo, hi = se.render_heatmap(cover, grid, b, overlay, max_dim=max_dim)
                        th, tw = rendered.shape[:2]
                        small = cv2.resize(expanded, (tw, th), interpolation=cv2.INTER_NEAREST)
                        expected = cv2.applyColorMap(small, cv2.COLORMAP_INFERNO)
                        if overlay:
                            base = cv2.resize(cover, (tw, th), interpolation=cv2.INTER_AREA)
                            expected = cv2.addWeighted(base, 0.4, expected, 0.6, 0)
                        np.testing.assert_array_equal(rendered, expected)
                        self.assertEqual((lo, hi), (0, grid.max()))

    def test_read_only_images_and_analysis_inputs_are_not_modified(self):
        res = se.stego_encode(image(), "immutability")
        arrays = (res.cover, res.img_out, res.positions)
        snapshots = [a.copy() for a in arrays]
        for a in arrays:
            a.flags.writeable = False
        counts = se.position_counts(res.shape, res.positions)
        counts.flags.writeable = False
        bins = se.bin_density(counts, 7)
        bins.flags.writeable = False
        before = bins.copy()
        np.testing.assert_array_equal(se.position_density(res.shape, res.positions, 7), bins)
        for overlay in (False, True):
            se.render_heatmap(res.cover, bins, 7, overlay=overlay, max_dim=23)
        se.compare(res.cover, res.img_out)
        se.diff_image(res.cover, res.img_out, 23)
        se.histogram_plot(res.cover, res.img_out)
        se.to_png_b64(res.img_out)
        for a, snapshot in zip(arrays, snapshots):
            np.testing.assert_array_equal(a, snapshot)
        np.testing.assert_array_equal(bins, before)
        self.assertEqual(se.stego_decode(res.img_out, res.token), "immutability")

    def test_invalid_and_empty_images_are_rejected(self):
        for bad in (None, [], np.empty((0, 3, 3), np.uint8), np.empty((3, 0, 3), np.uint8),
                    np.zeros((3, 3), np.uint8), np.zeros((3, 3, 4), np.uint8),
                    np.zeros((3, 3, 3), np.float32)):
            with self.subTest(image=bad):
                with self.assertRaises(se.StegoError):
                    se.stego_encode(bad, "x")
                with self.assertRaises(se.StegoError):
                    se.stego_decode(bad, se.new_token())
                with self.assertRaises(se.StegoError):
                    se.render_heatmap(bad, np.zeros((1, 1)), 10)

    def test_noncontiguous_image_roundtrip_and_immutability(self):
        cover = image()[:, ::-1]
        before = cover.copy()
        cover.flags.writeable = False
        res = se.stego_encode(cover, "strided image")
        self.assertEqual(se.stego_decode(res.img_out, res.token), "strided image")
        np.testing.assert_array_equal(cover, before)
        np.testing.assert_array_equal(res.cover, before)


class OriginalCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parent / "reference/stego_engine.py"
        if not path.exists():
            path = Path(__file__).resolve().parent.parent / "uploads/stego_engine.py"
        if not path.exists():
            raise unittest.SkipTest("Uploaded original required for source/compatibility checks")
        cls.original_source = path.read_text(encoding="utf-8")
        cls.current_source = Path(se.__file__).read_text(encoding="utf-8")
        spec = importlib.util.spec_from_file_location("stego_uploaded_original", path)
        cls.original = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.original
        spec.loader.exec_module(cls.original)

    def test_all_nonmaintenance_definitions_are_byte_and_ast_unchanged(self):
        self.assertEqual(len(self.original_source.splitlines()), 439)
        old_tree, new_tree = ast.parse(self.original_source), ast.parse(self.current_source)
        definitions = (ast.FunctionDef, ast.ClassDef)
        old = {node.name: node for node in old_tree.body if isinstance(node, definitions)}
        new = {node.name: node for node in new_tree.body if isinstance(node, definitions)}
        self.assertEqual(len(new), sum(isinstance(node, definitions) for node in new_tree.body))
        allowed = {"save_and_verify", "position_counts", "bin_density", "render_heatmap"}
        for name, node in old.items():
            if name in allowed:
                continue
            with self.subTest(name=name):
                self.assertEqual(ast.dump(node), ast.dump(new[name]))
                self.assertEqual(ast.get_source_segment(self.original_source, node).encode("utf-8"),
                                 ast.get_source_segment(self.current_source, new[name]).encode("utf-8"))
        old_constants = [ast.dump(n) for n in old_tree.body if isinstance(n, ast.Assign)]
        new_constants = [ast.dump(n) for n in new_tree.body if isinstance(n, ast.Assign)]
        self.assertEqual(old_constants, new_constants)
        self.assertEqual(ast.get_docstring(new_tree), ast.get_docstring(old_tree).replace("v1.x", "v0.x"))

    def test_deterministic_encoding_and_bidirectional_decode_compatibility(self):
        token = base64.urlsafe_b64encode(bytes(range(32))).decode()
        for message in ("", "hello", "\u65e5\u672c\u8a9e \U0001f642", "length boundary " * 20):
            with self.subTest(message=message):
                with (mock.patch.object(self.original, "new_token", return_value=token),
                      mock.patch.object(se, "new_token", return_value=token),
                      mock.patch("cryptography.fernet.time.time", return_value=1730000000),
                      mock.patch("cryptography.fernet.os.urandom", side_effect=lambda n: b"\x42" * n)):
                    old = self.original.stego_encode(image(), message)
                    new = se.stego_encode(image(), message)
                np.testing.assert_array_equal(old.img_out, new.img_out)
                np.testing.assert_array_equal(old.positions, new.positions)
                self.assertEqual(old.token, new.token)
                self.assertEqual(old.n_header_pixels, new.n_header_pixels)
                for engine in (self.original, se):
                    for result in (old, new):
                        self.assertEqual(engine.stego_decode(result.img_out, result.token), message)

    def test_token_format_variants_and_legacy_rejection_match_original(self):
        raw = bytes(range(32))
        token = base64.urlsafe_b64encode(raw).decode()
        legacy = base64.urlsafe_b64encode(b"\x00" * 16).decode()
        for engine in (self.original, se):
            self.assertEqual(len(engine.new_token()), 44)
            for text in (token, token.rstrip("="), " \n" + token + "\t"):
                self.assertEqual(engine.parse_token(text), raw)
            with self.assertRaisesRegex(engine.StegoError, r"v0\.x"):
                engine.parse_token(legacy)
        for text in ("", "!!!", "QUJD", "A" * 24, legacy):
            messages = []
            for engine in (self.original, se):
                with self.assertRaises(engine.StegoError) as error:
                    engine.parse_token(text)
                messages.append(str(error.exception))
            self.assertEqual(*messages)

    def test_read_normalization_matches_original(self):
        gray = np.array([[0, 1, 127], [128, 254, 255]], dtype=np.uint8)
        bgr = image(2, 3)
        bgra = np.dstack((bgr, gray))
        gray16 = np.array([[0, 255, 256], [257, 32768, 65535]], dtype=np.uint16)
        bgr16 = np.stack((gray16, 65535 - gray16, gray16 // 2), axis=2)
        fixtures = (("gray8", gray, np.repeat(gray[:, :, None], 3, axis=2)),
                    ("alpha8", bgra, bgr),
                    ("gray16", gray16, np.repeat((gray16 >> 8)[:, :, None], 3, axis=2).astype(np.uint8)),
                    ("bgr16", bgr16, (bgr16 >> 8).astype(np.uint8)))
        with tempfile.TemporaryDirectory() as directory:
            for name, source, expected in fixtures:
                with self.subTest(format=name):
                    path = Path(directory) / ("\u00fc_" + name + ".png")
                    ok, encoded = cv2.imencode(".png", source)
                    self.assertTrue(ok)
                    raw = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED)
                    self.assertEqual(raw.dtype, source.dtype)
                    np.testing.assert_array_equal(raw, source)
                    path.write_bytes(encoded.tobytes())
                    original = self.original.read_image(str(path))
                    current = se.read_image(str(path))
                    self.assertEqual(current.dtype, np.uint8)
                    self.assertEqual(current.shape, expected.shape)
                    np.testing.assert_array_equal(current, expected)
                    np.testing.assert_array_equal(current, original)

    def test_missing_corrupted_and_empty_reads_match_original(self):
        with tempfile.TemporaryDirectory() as directory:
            for name, contents, error in (("missing", None, "Cannot open file"),
                                           ("corrupted", b"not an image", "Unsupported or corrupted"),
                                           ("empty", b"", "File is empty or unreadable")):
                with self.subTest(file=name):
                    path = Path(directory) / (name + ".png")
                    if contents is not None:
                        path.write_bytes(contents)
                    messages = []
                    for engine in (self.original, se):
                        with self.assertRaisesRegex(engine.StegoError, error) as caught:
                            engine.read_image(str(path))
                        messages.append(str(caught.exception))
                    self.assertEqual(*messages)

    def test_v2_header_payload_and_authentication_layout(self):
        result = se.stego_encode(image(), "layout \u2713")
        fkey, _, mac = se._derive(se.parse_token(result.token))
        raw = se._extract(result.img_out.reshape(-1, 3), result.positions)
        self.assertEqual(raw[:4], b"ST2\x02")
        length = struct.unpack(">I", raw[4:8])[0]
        self.assertEqual(length, result.ciphertext_bytes)
        self.assertEqual(raw[8:16], se._tag(mac, length))
        self.assertEqual(se.Fernet(fkey).decrypt(raw[16:]).decode("utf-8"), "layout \u2713")
        self.assertEqual(result.n_header_pixels, 64)
        self.assertEqual(len(result.payload_positions), length * 4)
        for offset in (0, 3, 4, 8):
            bad_header = bytearray(raw[:16])
            bad_header[offset] ^= 1
            damaged = result.img_out.copy()
            se._embed(damaged.reshape(-1, 3), result.header_positions, bytes(bad_header))
            for engine in (self.original, se):
                with self.assertRaises(engine.StegoError):
                    engine.stego_decode(damaged, result.token)
        for length in (0, result.n_pixels_total // 4):
            header = b"ST2\x02" + struct.pack(">I", length) + se._tag(mac, length)
            damaged = result.img_out.copy()
            se._embed(damaged.reshape(-1, 3), result.header_positions, header)
            for engine in (self.original, se):
                with self.assertRaisesRegex(engine.StegoError, "length exceeds"):
                    engine.stego_decode(damaged, result.token)

    def test_original_analysis_and_aggregation_results_preserved(self):
        res = se.stego_encode(image(), "compatibility")
        self.assertEqual(se.compare(res.cover, res.img_out), self.original.compare(res.cover, res.img_out))
        for function in ("diff_image", "histogram_plot"):
            np.testing.assert_array_equal(getattr(se, function)(res.cover, res.img_out),
                                          getattr(self.original, function)(res.cover, res.img_out))
        rng = np.random.default_rng(89)
        for counts in (rng.integers(0, 5, (37, 63), dtype=np.int32),
                       rng.integers(2**35, 2**36, (37, 63), dtype=np.int64),
                       rng.integers(0, 100, (37, 63)) / 8):
            for b in (1, 4, 7, 64):
                np.testing.assert_array_equal(se.bin_density(counts, b), self.original.bin_density(counts, b))
        for b in (1, 7, 64):
            old_counts = self.original.position_counts(res.shape, res.positions)
            np.testing.assert_array_equal(se.position_counts(res.shape, res.positions), old_counts)
            np.testing.assert_array_equal(se.position_density(res.shape, res.positions, b),
                                          self.original.bin_density(old_counts, b))

    def test_aligned_heatmaps_match_original(self):
        cover = image(96, 128)
        bins = np.arange(12 * 16, dtype=float).reshape(12, 16)
        for max_dim in (64, 560):
            for overlay in (False, True):
                old = self.original.render_heatmap(cover, bins, 8, overlay, max_dim=max_dim)
                new = se.render_heatmap(cover, bins, 8, overlay, max_dim=max_dim)
                np.testing.assert_array_equal(old[0], new[0])
                self.assertEqual(old[1:], new[1:])


if __name__ == "__main__":
    unittest.main(verbosity=2)
