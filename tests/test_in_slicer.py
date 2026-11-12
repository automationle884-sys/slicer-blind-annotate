"""End-to-end test on the synthetic demo data. Run from the repository root:
  Slicer --no-main-window --no-splash --python-script tests/test_in_slicer.py
(drop --no-main-window to also test the wrong-view guard, which needs slice views).
Writes tests/result.txt; exits with code 1 on failure."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import traceback

import numpy as np
import slicer
import SimpleITK as sitk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(sys.argv[0])))
CFG = f'{ROOT}/examples/demo_config.json'
DATA = f'{ROOT}/examples/demo_data'
log = open(f'{ROOT}/tests/result.txt', 'w')
code = 0


def say(*a):
    print(*a, file=log, flush=True)


WARNINGS = []
slicer.util.warningDisplay = lambda msg, *a, **k: WARNINGS.append(msg)


def session(task, guard=False):
    spec = importlib.util.spec_from_file_location('blind_annotate', f'{ROOT}/blind_annotate.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    slicer.mrmlScene.Clear(0)
    s = mod.Session(mod.Config(CFG), 'case01', task, 'TEST')
    if not guard:   # points set from code are not on the visible slice; the guard is tested separately below
        s.on_plane = lambda *a, **k: True
    s.setup()
    return mod, s


try:
    if not os.path.exists(DATA):
        subprocess.check_call([sys.executable, f'{ROOT}/examples/make_demo_data.py'])
    out = f'{DATA}/case01/annotations/TEST'
    shutil.rmtree(out, ignore_errors=True)

    # paired points on two volumes: LPS round trip, pairs, resume
    mod, s = session('landmarks')
    L, R = s.views['left'], s.views['right']
    s.set_point(L['points'], 'vessel entry', mod.lps2ras([10.0, -20.0, 30.0]))
    s.set_point(R['points'], 'vessel entry', mod.lps2ras([12.0, -18.0, 27.0]))
    s.set_point(R['points'], 'organ tip', mod.lps2ras([1.0, 2.0, 3.0]))
    j = s.save()
    assert j['views']['left']['points']['vessel entry']['lps'] == [10.0, -20.0, 30.0]
    assert j['pairs'] == ['vessel entry'] and len(j['views']['right']['points']) == 2
    mod, s = session('landmarks')
    assert len(s.defined(s.views['right']['points'])) == 2
    say('landmarks: ok')

    # projection: pixel round trip and pixel values match the composite image
    mod, s = session('projection')
    P = s.views['right']
    s.set_point(P['points'], 'branch point', s.from_record(P, {'uv': [300, 200]}))
    j = s.save()
    assert j['views']['right']['points']['branch point']['uv'] == [300.0, 200.0]
    frames = np.load(f'{DATA}/case01/frames.npz')['frames']
    a, b = json.load(open(f'{DATA}/case01/frames.json'))['arterial']
    img = frames[a:b + 1].min(0)
    arr = slicer.util.arrayFromVolume(P['still'])
    assert arr[0, 200, 300] == img[200, 300] and arr[0, 50, 450] == img[50, 450]
    assert P['browser'].GetNumberOfItems() == len(frames)
    mod, s = session('projection')
    p = list(s.defined(s.views['right']['points']).values())[0]
    assert s.to_record(s.views['right'], p)['uv'] == [300.0, 200.0]
    say('projection: ok')

    # segmentation on a volume + curves on a projection
    mod, s = session('outline')
    V, C = s.views['left'], s.views['right']
    m = np.zeros(slicer.util.arrayFromVolume(V['still']).shape, np.uint8)
    m[30, 100, 120] = 1
    slicer.util.updateSegmentBinaryLabelmapFromArray(m, V['seg'], 'lesion', V['still'])
    n = s.new_curve(C, {'key': 'blush', 'closed': True})
    for uv in ([100, 100], [200, 120], [150, 220]):
        n.AddControlPoint(s.from_record(C, {'uv': uv}))
    j = s.save()
    assert [r['uv'] for r in j['views']['right']['curves']['blush'][0]] == [[100, 100], [200, 120], [150, 220]]
    lab = sitk.ReadImage(f'{out}/outline_left.nii.gz')
    ref = sitk.ReadImage(f'{DATA}/case01/mr.nii.gz')
    la = sitk.GetArrayFromImage(lab)
    assert la[30, 100, 120] == 1 and la.sum() == 1 and lab.GetSize() == ref.GetSize()
    assert np.allclose(lab.GetOrigin(), ref.GetOrigin(), atol=1e-3)
    mod, s = session('outline')
    assert len(s.views['right']['curves']) == 1
    say('outline: ok')

    # wrong-view guard (needs slice views, i.e. run with the main window)
    if slicer.app.layoutManager():
        mod, s = session('landmarks', guard=True)
        L, R = s.views['left'], s.views['right']
        lm = slicer.app.layoutManager()

        def on(view, x, y):
            return list(lm.sliceWidget(view).mrmlSliceNode().GetSliceToRAS().MultiplyPoint([x, y, 0, 1])[:3])
        s.set_point(L['points'], 'lesion centre', on('Red', 1, 1))
        slicer.app.processEvents()
        active = slicer.app.applicationLogic().GetSelectionNode().GetActivePlaceNodeID()
        assert active == R['points'].GetID(), 'did not switch to the other side'
        s.set_point(R['points'], 'lesion centre', on('Red', 5, 5))
        slicer.app.processEvents()
        assert 'lesion centre' not in s.defined(R['points']) and len(WARNINGS) == 1
        s.set_point(R['points'], 'lesion centre', on('Yellow', 5, 5))
        assert 'lesion centre' in s.defined(R['points'])
        say('wrong-view guard: ok')
    else:
        say('wrong-view guard: skipped (no main window)')
    shutil.rmtree(out, ignore_errors=True)
    say('ALL OK')
except Exception:
    traceback.print_exc(file=log)
    code = 1
log.close()
slicer.app.exit(code)
