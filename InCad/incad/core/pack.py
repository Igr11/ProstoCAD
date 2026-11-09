"""Saving an assembly (the user: "a folder named after the assembly is made at the place chosen, and every part of the
assembly goes into it"): Name.FCStd chosen in /where/ is saved as /where/Name/Name.FCStd, and every part file its
components come from (a sub-assembly's too) is saved into that folder; the links then point at the copies there
(FreeCAD writes a link as the path to its file as that file is now). The files the parts came from are left as they
were. Parts kept inside the assembly file (an assembly made in FreeCAD, a STEP) stay inside it."""
import os


def folder_for(path):
    """(folder, assembly file) for a chosen /where/Name.FCStd: /where/Name and /where/Name/Name.FCStd — or the folder
    the path is in already when that folder has the assembly's name."""
    where, name = os.path.split(os.path.abspath(path))
    stem = os.path.splitext(name)[0]
    if os.path.basename(where) == stem:
        return where, os.path.join(where, stem + ".FCStd")
    folder = os.path.join(where, stem)
    return folder, os.path.join(folder, stem + ".FCStd")


def part_docs(doc, seen=None):
    """The documents the assembly's components come from, each once (a sub-assembly's parts after it)."""
    from incad.core import assembly
    seen = [] if seen is None else seen
    for link in assembly.components(doc):
        linked = getattr(link, "LinkedObject", None)
        part = getattr(linked, "Document", None)
        if part is None or part is doc or part in seen:
            continue
        seen.append(part)
        if assembly.is_assembly(part):
            part_docs(part, seen)
    return seen


def lost(doc):
    """The components whose part file is not found (its link broken: the file is not where the assembly says)."""
    from incad.core import assembly
    return [c.Label for c in assembly.components(doc) if getattr(c, "LinkedObject", None) is None]


def lost_words(names):
    return ("%s: its part file is missing (looked for where the assembly says, beside it in its folder). Put the "
            "file back there, or delete the component" % ", ".join(names))


def packed(doc):
    """Is the assembly saved in a folder of its name with every part file in that folder?"""
    if not doc.FileName:
        return False
    folder, target = folder_for(doc.FileName)
    if os.path.abspath(doc.FileName) != target:
        return False
    return all(p.FileName and os.path.dirname(os.path.abspath(p.FileName)) == folder for p in part_docs(doc))


def _free_name(folder, name, taken):
    """A file name in the folder for one part: its own name — the file there is this assembly's copy, written over —
    unless another part of this save took it (two parts called the same from different folders)."""
    stem, ext = os.path.splitext(name)
    candidate, n = name, 2
    while candidate.lower() in taken:
        candidate = "%s_%d%s" % (stem, n, ext or ".FCStd")
        n += 1
    taken.add(candidate.lower())
    return os.path.join(folder, candidate)


def save(doc, path):
    """Save the assembly and its parts into the folder for `path`. Returns (folder, [(part label, file)]).
    The assembly goes to its new place first: FreeCAD writes a link as a path from the assembly's file as it is when
    the part is renamed, so parts saved before it moved were written as paths from its old place
    ('R180_Assembly/R180_Cover.FCStd' from inside R180_Assembly/: every part lost); then the parts, then the
    assembly once more with its links from where it is."""
    gone = lost(doc)
    if gone:  # saved without them they were silently gone from the assembly (the user)
        raise ValueError("Not saved. " + lost_words(gone))
    folder, target = folder_for(path)
    os.makedirs(folder, exist_ok=True)
    if os.path.abspath(doc.FileName or "") != target:
        doc.saveAs(target)
    taken = set()
    copied = []
    for part in part_docs(doc):
        inside = part.FileName and os.path.dirname(os.path.abspath(part.FileName)) == folder
        if inside:
            taken.add(os.path.basename(part.FileName).lower())
            if os.path.exists(part.FileName):
                part.save()
            else:  # its file is gone from the folder (FreeCAD's save backs the old file up first and failed: "No such
                part.saveAs(part.FileName)  # file or directory", the user): written there anew
            continue
        name = os.path.basename(part.FileName) if part.FileName else part.Label + ".FCStd"
        new = _free_name(folder, name, taken)
        part.saveAs(new)
        copied.append((part.Label, new))
    doc.save()
    return folder, copied
