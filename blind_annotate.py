"""Blind paired annotation in 3D Slicer, driven by a JSON config.

Two side-by-side views per task. Each view shows one image (a 3D volume, or a 2D projection
built from a frame sequence such as DSA/fluoroscopy) and carries one kind of annotation:
named points, segmentation, or curves. When both views carry points, they are annotated as
corresponding pairs. Only the images named in the config are loaded, so annotators never see
registration results or other derived data.

Run inside 3D Slicer:
  Slicer --python-script blind_annotate.py CONFIG.json CASE TASK --annotator A
Progress report (plain Python, no Slicer needed):
  python blind_annotate.py status CONFIG.json --annotator A

See README.md for the config format and output files.
"""
import datetime
import json
import os
import sys

__version__ = '0.1.0'
VIEWS = {'left': 'Red', 'right': 'Yellow'}


def option(argv, name, default=None):
    return argv[argv.index(name) + 1] if name in argv else default


class Config:
    def __init__(self, path):
        self.path = os.path.abspath(path)
        self.base = os.path.dirname(self.path)
        self.data = json.load(open(self.path, encoding='utf-8'))
        self.cases_root = os.path.join(self.base, self.data.get('cases_root', '.'))
        self.output = self.data.get('output', 'annotations/{annotator}')
        self.tasks = self.data['tasks']

    def cases(self):
        if 'cases' in self.data:
            return list(self.data['cases'])
        return sorted(d for d in os.listdir(self.cases_root)
                      if os.path.isdir(os.path.join(self.cases_root, d)) and not d.startswith('.'))

    def case_dir(self, case):
        return os.path.join(self.cases_root, case)

    def out_dir(self, case, annotator):
        return os.path.join(self.case_dir(case), self.output.format(annotator=annotator))

    def task_cases(self, task):
        return self.tasks[task].get('cases', self.cases())


def is_done(cfg, case, task, annotator):
    out = cfg.out_dir(case, annotator)
    names = [f'{task}.json'] + [f'{task}_{v}.nii.gz' for v in VIEWS]
    return any(os.path.exists(os.path.join(out, n)) for n in names)


def status(cfg, annotator):
    every = cfg.data.get('reannotate_every')
    for task, spec in cfg.tasks.items():
        cases = cfg.task_cases(task)
        done = [c for c in cases if is_done(cfg, c, task, annotator)]
        print(f'{task} ({spec.get("title", "")}): {len(done)}/{len(cases)} done by {annotator}')
        todo = [c for c in cases if c not in done]
        if todo:
            print('  next:', ' '.join(todo[:10]) + (' ...' if len(todo) > 10 else ''))
        if every:
            print(f'  second-annotator subset (every {every}th case):', ' '.join(sorted(cases)[::every]))


if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] == 'status':
    status(Config(sys.argv[2]), option(sys.argv, '--annotator', 'A'))
    sys.exit()

import numpy as np  # noqa: E402
import qt  # noqa: E402
import slicer  # noqa: E402
import vtk  # noqa: E402


def ras2lps(p):
    return [-p[0], -p[1], p[2]]


def lps2ras(p):
    return [-p[0], -p[1], p[2]]


def resolve(value, case_dir):
    """A literal, or {"file": ..., "key": ...[, "index": i or [i, j]]} read from a .json / .npz / .npy file
    in the case folder. A list index picks several elements, e.g. a frame range stored per case."""
    if not isinstance(value, dict):
        return value
    path = os.path.join(case_dir, value['file'])
    if path.endswith('.json'):
        v = json.load(open(path, encoding='utf-8'))[value['key']]
    else:
        v = np.load(path)
        v = v[value['key']] if 'key' in value else v
    v = np.asarray(v).tolist()
    idx = value.get('index')
    if idx is None:
        return v
    return [v[i] for i in idx] if isinstance(idx, list) else v[idx]


class Session:
    def __init__(self, cfg, case, task, annotator):
        self.cfg, self.case, self.task, self.annotator = cfg, case, task, annotator
        self.spec = cfg.tasks[task]
        self.dir = cfg.case_dir(case)
        self.out = cfg.out_dir(case, annotator)
        os.makedirs(self.out, exist_ok=True)
        self.layout = slicer.app.layoutManager()
        self.views = {}
        self.status = qt.QLabel()
        self.pair = None

    # ---------- images ----------
    def load_volume(self, spec, title):
        node = slicer.util.loadVolume(os.path.join(self.dir, spec['image']), {'name': title, 'show': False})
        if 'window' in spec:
            node.GetDisplayNode().SetAutoWindowLevel(False)
            node.GetDisplayNode().SetWindowLevel(*spec['window'])
        return {'still': node, 'kind': 'volume'}

    def load_projection(self, spec, title):
        p = spec['projection']
        path = os.path.join(self.dir, p['file'])
        spacing = None
        if path.endswith('.npz'):
            frames = np.load(path)[p.get('key', 'frames')]
        elif path.endswith('.npy'):
            frames = np.load(path)
        else:
            import SimpleITK as sitk
            img = sitk.ReadImage(path)
            frames, spacing = sitk.GetArrayFromImage(img), img.GetSpacing()[0]
        if frames.ndim == 2:
            frames = frames[None]
        if 'pixel_spacing' in p:
            spacing = resolve(p['pixel_spacing'], self.dir)
            spacing = spacing[0] if isinstance(spacing, list) else spacing
        spacing = float(spacing or 1.0)
        n = len(frames)
        a, b = 0, n - 1
        if 'frames' in p:
            a, b = [int(v) for v in resolve(p['frames'], self.dir)]
            a, b = max(0, min(a, n - 1)), max(0, min(b, n - 1))
        sel = frames[a:b + 1]
        img = {'min': sel.min(0), 'max': sel.max(0), 'mean': sel.mean(0), 'first': sel[0]}[p.get('composite', 'mean')]
        # IJK->RAS = diag(-s, -s, 1): in an axial view, image column u runs left->right and row v top->bottom,
        # i.e. the picture looks exactly as the original 2D image.
        m = slicer.util.vtkMatrixFromArray(np.diag([-spacing, -spacing, 1.0, 1.0]))
        vals = img[img > img.min()] if (img > img.min()).any() else img
        lo, hi = np.percentile(vals, [1, 99.5]) if 'window' not in spec else (None, None)

        def window(node):
            node.CreateDefaultDisplayNodes()
            node.GetDisplayNode().SetAutoWindowLevel(False)
            if 'window' in spec:
                node.GetDisplayNode().SetWindowLevel(*spec['window'])
            else:
                node.GetDisplayNode().SetWindowLevel(max(hi - lo, 1e-6), (hi + lo) / 2)

        still = slicer.util.addVolumeFromArray(img[None].astype(np.float32), name=title)
        still.SetIJKToRASMatrix(m)
        window(still)
        out = {'still': still, 'kind': 'projection', 'proxy': None, 'browser': None}
        if n > 1:
            seq = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSequenceNode', f'{title} frames')
            seq.SetIndexName('frame')
            seq.SetIndexUnit('')
            tmp = slicer.util.addVolumeFromArray(frames[:1].astype(np.float32), name='tmp')
            tmp.SetIJKToRASMatrix(m)
            for k in range(n):
                slicer.util.updateVolumeFromArray(tmp, frames[k:k + 1].astype(np.float32))
                seq.SetDataNodeAtValue(tmp, str(k))
            slicer.mrmlScene.RemoveNode(tmp)
            br = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSequenceBrowserNode', f'{title} player')
            br.SetAndObserveMasterSequenceNodeID(seq.GetID())
            if 'frame_rate' in p:
                br.SetPlaybackRateFps(float(resolve(p['frame_rate'], self.dir)))
            slicer.modules.sequences.logic().UpdateProxyNodesFromSequences(br)
            proxy = br.GetProxyNode(seq)
            proxy.SetName(f'{title} (all frames)')
            window(proxy)
            if self.layout:
                slicer.modules.sequences.setToolBarActiveBrowserNode(br)
            out.update(proxy=proxy, browser=br)
        return out

    def show(self, view, node):
        if not self.layout:
            return
        w = self.layout.sliceWidget(view)
        w.mrmlSliceNode().SetOrientation('Axial')
        comp = w.sliceLogic().GetSliceCompositeNode()
        comp.SetLinkedControl(False)
        comp.SetBackgroundVolumeID(node.GetID())
        comp.SetForegroundVolumeID(None)
        w.sliceLogic().FitSliceToAll()

    def start_at_mask(self, view, path):
        if not self.layout or not os.path.exists(os.path.join(self.dir, path)):
            return
        import SimpleITK as sitk
        m = sitk.ReadImage(os.path.join(self.dir, path))
        idx = np.argwhere(sitk.GetArrayFromImage(m) > 0)
        if not len(idx):
            return
        x, y, z = m.TransformContinuousIndexToPhysicalPoint(idx.mean(0)[::-1].tolist())
        self.layout.sliceWidget(view).mrmlSliceNode().JumpSliceByCentering(-x, -y, z)

    # ---------- coordinates ----------
    def to_record(self, v, ras):
        """World RAS -> what is written to disk: LPS mm for volumes, pixel (u, v) [+ frame] for projections."""
        if v['kind'] == 'volume':
            return {'lps': [round(c, 3) for c in ras2lps(ras)]}
        m = vtk.vtkMatrix4x4()
        v['still'].GetRASToIJKMatrix(m)
        i, j, _, _ = m.MultiplyPoint(list(ras) + [1.0])
        frame = None
        if v['proxy'] is not None and self.background(v) == v['proxy'].GetID():
            frame = v['browser'].GetSelectedItemNumber()
        return {'uv': [round(i, 2), round(j, 2)], 'frame': frame}

    def from_record(self, v, rec):
        if 'lps' in rec:
            return lps2ras(rec['lps'])
        m = vtk.vtkMatrix4x4()
        v['still'].GetIJKToRASMatrix(m)
        return m.MultiplyPoint([rec['uv'][0], rec['uv'][1], 0, 1.0])[:3]

    def background(self, v):
        if not self.layout:
            return v['still'].GetID()
        return self.layout.sliceWidget(v['view']).sliceLogic().GetSliceCompositeNode().GetBackgroundVolumeID()

    def on_plane(self, view, ras, tol=0.6):
        """True if the point lies on the current slice plane of `view`; catches clicks made in the other view."""
        if not self.layout:
            return True
        m = vtk.vtkMatrix4x4()
        vtk.vtkMatrix4x4.Invert(self.layout.sliceWidget(view).mrmlSliceNode().GetSliceToRAS(), m)
        return abs(m.MultiplyPoint(list(ras) + [1.0])[2]) < tol

    # ---------- markups ----------
    @staticmethod
    def defined(n):
        out = {}
        for i in range(n.GetNumberOfControlPoints()):
            if n.GetNthControlPointPositionStatus(i) == slicer.vtkMRMLMarkupsNode.PositionDefined:
                p = [0.0, 0.0, 0.0]
                n.GetNthControlPointPositionWorld(i, p)
                out[n.GetNthControlPointLabel(i)] = p
        return out

    @staticmethod
    def set_point(n, label, ras):
        for i in range(n.GetNumberOfControlPoints()):
            if n.GetNthControlPointLabel(i) == label:
                n.SetNthControlPointPositionWorld(i, *ras)
                return
        n.AddControlPoint(list(ras), label)

    def start_placing(self, n):
        sel = slicer.app.applicationLogic().GetSelectionNode()
        sel.SetReferenceActivePlaceNodeClassName(n.GetClassName())
        sel.SetActivePlaceNodeID(n.GetID())
        inter = slicer.app.applicationLogic().GetInteractionNode()
        inter.SetPlaceModePersistence(1)
        inter.SetCurrentInteractionMode(inter.Place)

    def points_node(self, v, labels):
        n = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLMarkupsFiducialNode', v['title'])
        for lb in labels:
            i = n.AddControlPoint([0.0, 0.0, 0.0], lb)
            n.UnsetNthControlPointPosition(i)
        n.GetDisplayNode().SetViewNodeIDs([f'vtkMRMLSliceNode{v["view"]}'])
        n.GetDisplayNode().SetTextScale(2.5)
        return n

    def watch_points(self, v):
        """Undo points that land in the wrong view; in paired mode, jump to the other side after each point."""
        n = v['points']
        n._checked = set(self.defined(n))

        def on_defined(caller, event):
            for lb, p in self.defined(n).items():
                if lb in n._checked:
                    continue
                n._checked.add(lb)
                if not self.on_plane(v['view'], p):
                    i = [n.GetNthControlPointLabel(k) for k in range(n.GetNumberOfControlPoints())].index(lb)
                    n.UnsetNthControlPointPosition(i)
                    n._checked.discard(lb)
                    slicer.util.warningDisplay(f'"{lb}" was placed in the other view and has been undone. '
                                               f'Place it in the {v["title"]} view.')
                    return
                other = self.pair and next((o for o in self.pair if o is not v), None)
                if other is not None and lb not in self.defined(other['points']):
                    self.start_placing(other['points'])
            self.update_status()

        def on_removed(caller, event):
            n._checked = set(self.defined(n))
            self.update_status()
        n.AddObserver(slicer.vtkMRMLMarkupsNode.PointPositionDefinedEvent, on_defined)
        n.AddObserver(slicer.vtkMRMLMarkupsNode.PointRemovedEvent, on_removed)

    def new_curve(self, v, cspec):
        cls = 'vtkMRMLMarkupsClosedCurveNode' if cspec.get('closed') else 'vtkMRMLMarkupsCurveNode'
        n = slicer.mrmlScene.AddNewNodeByClass(cls, cspec.get('title', cspec['key']))
        n.GetDisplayNode().SetViewNodeIDs([f'vtkMRMLSliceNode{v["view"]}'])
        v['curves'].append((cspec['key'], n))
        return n

    # ---------- segmentation ----------
    def seg_path(self, side, ext):
        return os.path.join(self.out, f'{self.task}_{side}{ext}')

    def segmentation(self, side, v, segments):
        old = self.seg_path(side, '.seg.nrrd')
        if os.path.exists(old):
            seg = slicer.util.loadSegmentation(old)
        else:
            seg = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLSegmentationNode')
            seg.SetReferenceImageGeometryParameterFromVolumeNode(v['still'])
            for s in segments:
                seg.GetSegmentation().AddEmptySegment(s, s)
        seg.SetName(f'{self.task} {v["title"]}')
        seg.CreateDefaultDisplayNodes()
        return seg

    def open_editor(self, v):
        if not self.layout:
            return
        slicer.util.selectModule('SegmentEditor')
        ed = slicer.modules.segmenteditor.widgetRepresentation().self().editor
        ed.setSegmentationNode(v['seg'])
        (ed.setSourceVolumeNode if hasattr(ed, 'setSourceVolumeNode') else ed.setMasterVolumeNode)(v['still'])

    # ---------- setup / save ----------
    def setup(self):
        if self.layout:
            self.layout.setLayout(slicer.vtkMRMLLayoutNode.SlicerLayoutSideBySideView)
        old = os.path.join(self.out, f'{self.task}.json')
        old = json.load(open(old, encoding='utf-8'))['views'] if os.path.exists(old) else {}
        for side, view in VIEWS.items():
            spec = self.spec[side]
            title = spec.get('title', side)
            v = self.load_projection(spec, title) if 'projection' in spec else self.load_volume(spec, title)
            v.update(view=view, title=title, spec=spec, curves=[])
            self.views[side] = v
            self.show(view, v['still'])
            if 'start_at_mask' in spec:
                self.start_at_mask(view, spec['start_at_mask'])
            prev = old.get(side, {})
            if 'points' in spec:
                v['points'] = self.points_node(v, spec['points'])
                for lb, rec in prev.get('points', {}).items():
                    self.set_point(v['points'], lb, self.from_record(v, rec))
            if 'curves' in spec:
                for cspec in spec['curves']:
                    for c in prev.get('curves', {}).get(cspec['key'], []):
                        n = self.new_curve(v, cspec)
                        for rec in c:
                            n.AddControlPoint(self.from_record(v, rec))
            if 'segments' in spec:
                v['seg'] = self.segmentation(side, v, spec['segments'])
        pts = [v for v in self.views.values() if 'points' in v]
        self.pair = pts if len(pts) == 2 else None
        for v in pts:
            self.watch_points(v)
        segs = [v for v in self.views.values() if 'seg' in v]
        if segs:
            self.open_editor(segs[0])
            for v in self.views.values():          # the Segment Editor resets every view's background
                self.show(v['view'], v['still'])
        elif pts and self.layout:
            slicer.util.selectModule('Markups')
            slicer.modules.markups.logic().SetActiveList(pts[0]['points'])
        if self.spec.get('link_views') and self.layout:
            for view in VIEWS.values():
                self.layout.sliceWidget(view).sliceLogic().GetSliceCompositeNode().SetLinkedControl(True)
        if pts:
            first = min(pts, key=lambda v: len(self.defined(v['points'])))
            self.start_placing(first['points'])

    def save(self):
        views = {}
        for side, v in self.views.items():
            rec = {'title': v['title'], 'kind': v['kind']}
            if 'points' in v:
                rec['points'] = {lb: self.to_record(v, p) for lb, p in self.defined(v['points']).items()}
            if v['curves']:
                rec['curves'] = {}
                for key, n in v['curves']:
                    if slicer.mrmlScene.IsNodePresent(n) and n.GetNumberOfDefinedControlPoints() >= 2:
                        rec['curves'].setdefault(key, []).append(
                            [self.to_record(v, p) for p in self.defined_list(n)])
            if 'seg' in v:
                self.save_seg(side, v)
                rec['segmentation'] = os.path.basename(self.seg_path(side, '.nii.gz'))
            views[side] = rec
        out = {'case': self.case, 'task': self.task, 'annotator': self.annotator,
               'saved': datetime.datetime.now().isoformat(timespec='seconds'), 'tool_version': __version__,
               'views': views}
        if self.pair:
            out['pairs'] = sorted(set(views['left']['points']) & set(views['right']['points']))
        json.dump(out, open(os.path.join(self.out, f'{self.task}.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        self.status.setText(f'Saved {datetime.datetime.now():%H:%M:%S} -> {self.out}')
        return out

    @staticmethod
    def defined_list(n):
        out = []
        for i in range(n.GetNumberOfControlPoints()):
            p = [0.0, 0.0, 0.0]
            n.GetNthControlPointPositionWorld(i, p)
            out.append(p)
        return out

    def save_seg(self, side, v):
        slicer.util.saveNode(v['seg'], self.seg_path(side, '.seg.nrrd'))
        lm = slicer.mrmlScene.AddNewNodeByClass('vtkMRMLLabelMapVolumeNode')
        ids = vtk.vtkStringArray()
        v['seg'].GetSegmentation().GetSegmentIDs(ids)
        slicer.modules.segmentations.logic().ExportSegmentsToLabelmapNode(v['seg'], ids, lm, v['still'])
        slicer.util.saveNode(lm, self.seg_path(side, '.nii.gz'))
        slicer.mrmlScene.RemoveNode(lm)

    # ---------- UI ----------
    def update_status(self):
        if not self.pair:
            return
        a, b = (self.defined(v['points']) for v in self.pair)
        need = self.spec.get('min_pairs')
        self.status.setText(f'{len(set(a) & set(b))} pairs' + (f' (need >= {need})' if need else '') +
                            f' - {self.pair[0]["title"]}: {len(a)}, {self.pair[1]["title"]}: {len(b)}. '
                            'Only points with the same name on both sides form a pair.')

    def toggle_frames(self, v):
        comp = self.layout.sliceWidget(v['view']).sliceLogic().GetSliceCompositeNode()
        nxt = v['proxy'] if comp.GetBackgroundVolumeID() == v['still'].GetID() else v['still']
        comp.SetBackgroundVolumeID(nxt.GetID())
        self.status.setText('Showing all frames: use the Sequences toolbar to play or step.'
                            if nxt is v['proxy'] else 'Showing the composite image.')

    def build_panel(self):
        w = qt.QWidget()
        lay = qt.QVBoxLayout(w)
        t = qt.QLabel(f'{self.case} - {self.spec.get("title", self.task)} - annotator {self.annotator}\n\n'
                      + self.spec.get('instructions', ''))
        t.setWordWrap(True)
        lay.addWidget(t)
        for v in self.views.values():
            if 'points' in v:
                b = qt.QPushButton(f'Place points on {v["title"]}')
                b.connect('clicked()', lambda v=v: self.start_placing(v['points']))
                lay.addWidget(b)
            if v.get('proxy') is not None:
                b = qt.QPushButton(f'{v["title"]}: composite / all frames')
                b.connect('clicked()', lambda v=v: self.toggle_frames(v))
                lay.addWidget(b)
            for cspec in v['spec'].get('curves', []):
                b = qt.QPushButton(f'New {cspec.get("title", cspec["key"])} ({"closed" if cspec.get("closed") else "open"})')
                b.connect('clicked()', lambda v=v, c=cspec: self.start_placing(self.new_curve(v, c)))
                lay.addWidget(b)
        s = qt.QPushButton('Save (Ctrl+S)')
        s.connect('clicked()', self.save)
        lay.addWidget(s)
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        lay.addStretch()
        d = qt.QDockWidget('Annotation')
        d.setWidget(w)
        slicer.util.mainWindow().addDockWidget(qt.Qt.RightDockWidgetArea, d)
        self.shortcut = qt.QShortcut(qt.QKeySequence('Ctrl+S'), slicer.util.mainWindow())
        self.shortcut.connect('activated()', self.save)
        self.update_status()


def main(argv):
    pos = [a for i, a in enumerate(argv) if not a.startswith('--') and (i == 0 or not argv[i - 1].startswith('--'))]
    cfg_path, case, task = pos[:3]
    s = Session(Config(cfg_path), case, task, option(argv, '--annotator', 'A'))
    s.setup()
    if s.layout:
        s.build_panel()
    shot = option(argv, '--screenshot')
    if shot:   # used by tests: grab the window and quit
        slicer.app.processEvents()
        qt.QTimer.singleShot(1500, lambda: (slicer.util.mainWindow().grab().save(shot), slicer.app.exit(0)))
    return s


if __name__ == '__main__':
    SESSION = main(sys.argv[1:])
