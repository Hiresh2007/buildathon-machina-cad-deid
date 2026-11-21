"""Physical names, PDF titles, reference paths and older attempt reports agree."""
from pathlib import Path, PurePosixPath
import posixpath
import tempfile
import unittest

from naming import repository_parts
from pdf_identity import asset_title, resolve, restore_directory_forms
from pipeline import Identities, PathIndex


class RepositoryPaths(unittest.TestCase):
    def test_shared_owner_keeps_one_pseudonym(self):
        names = [
            "sources/github/AtlasLab__ProjectX/drawings/model.step",
            "sources/github/AtlasLab__ProjectQ/drawings/model.step",
            "sources/github/OtherLab__ProjectX/drawings/model.step",
            "sources/github/model__cad/drawings/model.step",
        ]
        paths = [PurePosixPath(n) for n in names]
        ids = Identities(bytes(32))
        resolve([], ids, paths)
        index = PathIndex(paths, ids)
        folders = [PurePosixPath(index.targets[n]).parts[2] for n in names]
        owner = ids.alias("AtlasLab")
        first = owner + "__" + ids.alias("ProjectX")
        self.assertEqual(folders[0], first)
        self.assertEqual(folders[1].split("__")[0], owner)
        self.assertEqual(folders[2], ids.alias("OtherLab") + "__" + ids.alias("ProjectX"))
        self.assertEqual(folders[3], ids.alias("model") + "__" + ids.alias("cad"))
        self.assertEqual(len(set(folders)), len(folders))
        self.assertEqual(ids.redact_text("Source model__cad"), "Source " + folders[3])
        self.assertEqual(ids.redact_text("Source AtlasLab__ProjectX"), "Source " + first)
        for path in names:
            self.assertEqual(asset_title(path, ids.alias), index.targets[path])
        ref = posixpath.relpath(names[1], posixpath.dirname(names[0]))
        rewritten = index.reference(names[0], ref)
        self.assertEqual(posixpath.normpath(posixpath.join(posixpath.dirname(index.targets[names[0]]), rewritten)), index.targets[names[1]])
        self.assertEqual(ids.redact_text("Use the model and CAD drawings"), "Use the model and CAD drawings")

    def test_named_artifact_directory_inside_known_project(self):
        ids = Identities(bytes(32))
        names = ["sources/github/AtlasLab__Pump/Technical Drawings/model.step",
                 "sources/github/AtlasLab__Pump/Fictional Client/model.step",
                 "sources/github/AtlasLab__Pump/photos.step",
                 "sources/github/AtlasLab/pdfs/model.step"]
        paths = list(map(PurePosixPath, names))
        resolve([], ids, paths)
        index = PathIndex(paths, ids)
        self.assertEqual(PurePosixPath(index.targets[names[0]]).parts[3], "Technical Drawings")
        self.assertNotIn("Fictional Client", index.targets[names[1]])
        self.assertNotIn("pdfs", index.targets[names[3]])
        self.assertEqual(asset_title(names[0], ids.alias), index.targets[names[0]])
        ids.preserve_repository_folders = False
        previous = PathIndex(paths, ids)
        self.assertNotIn("Technical Drawings", previous.targets[names[0]])
        self.assertEqual(asset_title(names[0], ids.alias), previous.targets[names[0]])

    def test_unknown_layout_and_old_attempt(self):
        for raw, parents in [
            ("User__Repo", ("sources", "web")), ("User__Repo", ("private", "github")),
            ("a__b__c", ("sources", "github")), ("a_b__c", ("sources", "github")),
            ("a__..", ("sources", "github")), ("a__repo.git/bad", ("sources", "github")),
        ]:
            self.assertIsNone(repository_parts(raw, parents))
        with tempfile.TemporaryDirectory() as folder:
            attempt = Path(folder)
            ids = Identities(bytes(32))
            restore_directory_forms(attempt / "input", attempt, [], ids)
            name = "sources/github/AtlasLab__ProjectX/drawings/model.step"
            path = PurePosixPath(name)
            index = PathIndex([path], ids)
            self.assertFalse(ids.link_repositories)
            self.assertEqual(PurePosixPath(index.targets[name]).parts[2], ids.alias("AtlasLab__ProjectX"))
            self.assertEqual(asset_title(name, ids.alias), index.targets[name])


if __name__ == "__main__":
    unittest.main()
