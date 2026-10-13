import unittest
import os
from unittest.mock import patch
import numpy as np
import cv2
from PIL import Image
import subtitle_core as sc

class Frames:
    def __init__(self, frames):
        self.frames=frames; self.index=-1
    def isOpened(self): return True
    def get(self, prop):
        return {cv2.CAP_PROP_FPS:25.,cv2.CAP_PROP_FRAME_COUNT:len(self.frames),
                cv2.CAP_PROP_FRAME_WIDTH:self.frames[0].shape[1],cv2.CAP_PROP_FRAME_HEIGHT:self.frames[0].shape[0]}[prop]
    def grab(self): self.index+=1; return self.index<len(self.frames)
    def retrieve(self): return True,self.frames[self.index].copy()
    def release(self): pass

class DedupTests(unittest.TestCase):
    def extract(self, frames, **kwargs):
        offset=frames[0].shape[0]-151
        with patch.object(sc.cv2,'VideoCapture',side_effect=lambda _:Frames(frames)):
            return sc.extract_subtitle_frames('memory',offset+60,offset+110,crop_y1=offset,crop_y2=offset+151,
                  allow_expand=False,log=lambda _:None,**kwargs)
    @unittest.skipUnless(os.environ.get('ZIMU_DEDUP_REAL_PNG'), 'Set ZIMU_DEDUP_REAL_PNG to the 2026-10-12 original long PNG')
    def test_real_background_changes(self):
        im=np.array(Image.open(os.environ['ZIMU_DEDUP_REAL_PNG']).convert('RGB'))
        # Real consecutive outputs: 28 strips contain 17 unique sentences.
        strips=[]
        for i in range(28):
            frame=np.zeros((1080,1920,3),np.uint8)
            frame[-151:]=cv2.cvtColor(im[1082+i*153:1233+i*153],cv2.COLOR_RGB2BGR)
            strips.append(frame)
        for outline in ['dark','none']:
            with self.subTest(outline=outline):
                frames,times,crop=self.extract([s for s in strips for _ in range(25)],outline=outline)
                self.assertEqual(len(frames),17)
                self.assertEqual(len(times),17)
                self.assertEqual(crop,(0,929,1920,1080))
    def test_one_letter_change_is_not_merged(self):
        frames=[]
        for text in ['HELLO WORLD','HELLO WORLE']:
            f=np.full((151,700,3),70,np.uint8)
            cv2.putText(f,text,(10,99),cv2.FONT_HERSHEY_SIMPLEX,1.2,(0,0,0),5)
            cv2.putText(f,text,(10,99),cv2.FONT_HERSHEY_SIMPLEX,1.2,(245,245,245),2)
            frames += [f]*25
        result,_,_=self.extract(frames)
        self.assertEqual(len(result),2)

    @unittest.skipUnless(os.environ.get('ZIMU_DEDUP_NEW_PNG'), 'Set ZIMU_DEDUP_NEW_PNG to the 2026-10-13 original long PNG')
    def test_real_moving_bright_diagonal(self):
        im=np.array(Image.open(os.environ['ZIMU_DEDUP_NEW_PNG']).convert('RGB'))
        sequence=[]
        for i in range(9):
            f=np.zeros((1080,1920,3),np.uint8)
            f[980:1052]=cv2.cvtColor(im[1054+i*74:1126+i*74],cv2.COLOR_RGB2BGR)
            sequence += [f]*25
        for outline in ['dark','none']:
            for lo,hi in [(0,72),(10,60),(15,58)]:
                with self.subTest(outline=outline,band=(lo,hi)):
                    with patch.object(sc.cv2,'VideoCapture',side_effect=lambda _:Frames(sequence)):
                        frames,times,crop=sc.extract_subtitle_frames('memory',980+lo,980+hi,
                            crop_y1=980,crop_y2=1052,allow_expand=False,outline=outline,log=lambda _:None)
                    self.assertEqual(len(frames),6)
                    self.assertEqual(len(times),6)

    def test_moving_background_does_not_change_subtitle(self):
        sequence=[]
        for shift in [0,8,16]:
            f=np.full((151,700,3),70,np.uint8)
            cv2.fillPoly(f,[np.array([[100+shift,0],[140+shift,0],[490+shift,151],[450+shift,151]],np.int32)],(245,245,245))
            cv2.putText(f,'HELLO WORLD',(10,99),cv2.FONT_HERSHEY_SIMPLEX,1.2,(0,0,0),5)
            cv2.putText(f,'HELLO WORLD',(10,99),cv2.FONT_HERSHEY_SIMPLEX,1.2,(245,245,245),2)
            sequence += [f]*25
        result,_,_=self.extract(sequence)
        self.assertEqual(len(result),1)
    def test_later_repeat_is_preserved(self):
        f=np.full((151,700,3),70,np.uint8)
        cv2.putText(f,'HELLO',(20,99),cv2.FONT_HERSHEY_SIMPLEX,1.2,(0,0,0),5)
        cv2.putText(f,'HELLO',(20,99),cv2.FONT_HERSHEY_SIMPLEX,1.2,(245,245,245),2)
        blank=np.full_like(f,70)
        result,_,_=self.extract([f]*25+[blank]*125+[f]*25)
        self.assertEqual(len(result),2)

    def test_same_text_survives_bright_and_dark_background(self):
        frames=[]
        for bg in [30,195,120,230]:
            f=np.full((151,700,3),bg,np.uint8)
            # Thin black border only touches the edges of wide white strokes.
            cv2.putText(f,'SAME SUBTITLE',(10,99),cv2.FONT_HERSHEY_SIMPLEX,1.2,(10,10,10),8)
            cv2.putText(f,'SAME SUBTITLE',(10,99),cv2.FONT_HERSHEY_SIMPLEX,1.2,(245,245,245),6)
            frames += [f]*25
        result,_,_=self.extract(frames)
        self.assertEqual(len(result),1)

    def test_low_brightness_fallback_and_short_hole(self):
        f=np.full((151,700,3),30,np.uint8)
        cv2.putText(f,'DIM SUBTITLE',(10,99),cv2.FONT_HERSHEY_SIMPLEX,1.2,(0,0,0),5)
        cv2.putText(f,'DIM SUBTITLE',(10,99),cv2.FONT_HERSHEY_SIMPLEX,1.2,(205,205,205),2)
        blank=np.full_like(f,30)
        result,_,_=self.extract([f]*25+[blank]*10+[f]*25)
        self.assertEqual(len(result),1)

if __name__=='__main__': unittest.main()
