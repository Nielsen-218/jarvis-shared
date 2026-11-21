"""Zoomable local box editor for the existing Nolagin human-label workflow."""
from __future__ import annotations
from dataclasses import dataclass,field


def box_overlap(a,b):
    intersection=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
    area_a=(a[2]-a[0])*(a[3]-a[1]);area_b=(b[2]-b[0])*(b[3]-b[1])
    return intersection/max(1,area_a+area_b-intersection),intersection/max(1,min(area_a,area_b))


def annotation_issues(boxes):
    """Flag ambiguous geometry for correction; never repair human labels silently."""
    issues=[]
    if len(boxes)>9:issues.append('More than 9 boxes: remove portraits and duplicate proposals')
    if any(b[2]-b[0]<2 or b[3]-b[1]<2 for b in boxes):issues.append('Accidental line/tiny box: redraw or skip this frame')
    for i,box in enumerate(boxes):
        for previous in boxes[:i]:
            iou,contained=box_overlap(box,previous)
            if iou>=.85 or (iou>=.5 and contained>=.95):
                issues.append('Overlapping duplicate/nested boxes: review each individual enemy')
                return issues
    return issues


@dataclass
class BoxView:
    width:int
    height:int
    boxes:list=field(default_factory=list)
    view:tuple|None=None
    confirmed:set=field(default_factory=set)

    def __post_init__(self):
        self.view=self.view or (0,0,self.width,self.height)
        original=self.boxes;self.boxes=[]
        for box in original:self.add(box,confirmed=False)

    def layout(self):
        x1,y1,x2,y2=self.view
        scale=min(1280/(x2-x1),700/(y2-y1))
        return scale,(1280-(x2-x1)*scale)/2,55+(700-(y2-y1)*scale)/2

    def source(self,x,y):
        scale,left,top=self.layout();x1,y1,x2,y2=self.view
        point=(x1+(x-left)/scale,y1+(y-top)/scale)
        return point if x1<=point[0]<=x2 and y1<=point[1]<=y2 else None

    def screen(self,x,y):
        scale,left,top=self.layout();x1,y1,_,_=self.view
        return round(left+(x-x1)*scale),round(top+(y-y1)*scale)

    def region(self,a,b):
        x1,x2=sorted((a[0],b[0]));y1,y2=sorted((a[1],b[1]))
        return (max(0,round(x1)),max(0,round(y1)),min(self.width,round(x2)),min(self.height,round(y2)))

    def add(self,box,*,confirmed=True):
        box=list(self.region(box[:2],box[2:]))
        if box[0]<box[2] and box[1]<box[3]:
            if not confirmed and any(box_overlap(box,b)[0]>=.85 for b in self.boxes):return
            if box not in self.boxes:self.boxes.append(box)
            if confirmed:self.confirmed.add(tuple(box))

    def toggle(self,point):
        hits=[b for b in self.boxes if b[0]<=point[0]<=b[2] and b[1]<=point[1]<=b[3]]
        if not hits:return
        box=tuple(min(hits,key=lambda b:(b[2]-b[0])*(b[3]-b[1])))
        if box in self.confirmed:self.confirmed.remove(box)
        else:self.confirmed.add(box)

    def selected(self):
        return [b[:] for b in self.boxes if tuple(b) in self.confirmed]

    def remove(self,box):
        self.boxes.remove(box)
        self.confirmed.discard(tuple(box))

    def zoom(self,factor,anchor=None):
        x1,y1,x2,y2=self.view
        anchor=anchor or ((x1+x2)/2,(y1+y2)/2)
        w=max(12,min(self.width,(x2-x1)/factor));h=max(12,min(self.height,(y2-y1)/factor))
        left=max(0,min(self.width-w,anchor[0]-w*(anchor[0]-x1)/(x2-x1)))
        top=max(0,min(self.height-h,anchor[1]-h*(anchor[1]-y1)/(y2-y1)))
        self.view=(round(left),round(top),round(left+w),round(top+h))


def key_action(key:int)->str|None:
    if key in (10,13):return 'save'
    if key in (27,ord('q')):return 'cancel'
    return {ord('u'):'undo',ord('c'):'clear',ord('+'):'plus',ord('='):'plus',
            ord('-'):'minus',ord('0'):'reset',ord('p'):'suggest'}.get(key)


def edit_boxes(frame,initial:list,*,suggest=None,initial_view=None)->list|None:
    """Enter/click Save ends editing ONCE. Drag adds; wheel/right-drag zooms.

    Suggestions are drafts for human correction, never labels by themselves.
    """
    import cv2,numpy as np
    state=BoxView(frame.shape[1],frame.shape[0],initial,initial_view)
    title='Nolagin enemy boxes: CLICK real enemies GREEN, then SAVE / ENTER'
    cv2.namedWindow(title,cv2.WINDOW_NORMAL);cv2.resizeWindow(title,1280,800)
    buttons=[];x=8
    for text,action,width in [('Save [Enter]','save',145),('Undo [U]','undo',105),
            ('Clear [C]','clear',105),('Zoom +','plus',105),('Zoom -','minus',105),
            ('Reset [0]','reset',110),('Suggest [P]','suggest',145),('Cancel [Esc]','cancel',155)]:
        if action!='suggest' or suggest is not None:buttons.append((x,x+width,text,action))
        x+=width+5
    interaction={'start':None,'button':None,'end':None,'action':None,'dirty':True}
    message='Click each real enemy box GREEN. Orange boxes are ignored. Drag adds missing enemies; right-drag zooms.'

    def mouse(event,x,y,flags,_):
        point=state.source(x,y)
        if event==cv2.EVENT_LBUTTONUP and y<50:
            interaction['action']=next((action for left,right,_,action in buttons if left<=x<=right),None)
        elif event in (cv2.EVENT_LBUTTONDOWN,cv2.EVENT_RBUTTONDOWN) and point:
            interaction.update(start=point,end=point,button=event)
        elif event==cv2.EVENT_MOUSEMOVE and interaction['start'] and point:interaction['end']=point
        elif event in (cv2.EVENT_LBUTTONUP,cv2.EVENT_RBUTTONUP) and interaction['start']:
            start=interaction['start'];end=point or interaction['end'];region=state.region(start,end)
            if interaction['button']==cv2.EVENT_LBUTTONDOWN:
                if abs(start[0]-end[0])+abs(start[1]-end[1])<5:state.toggle(start)
                else:state.add(region)
            elif abs(start[0]-end[0])+abs(start[1]-end[1])<5:
                hits=[b for b in state.boxes if b[0]<=start[0]<=b[2] and b[1]<=start[1]<=b[3]]
                if hits:state.remove(min(hits,key=lambda b:(b[2]-b[0])*(b[3]-b[1])))
            elif region[2]-region[0]>3 and region[3]-region[1]>3:state.view=region
            interaction.update(start=None,end=None,button=None)
        elif event==cv2.EVENT_MOUSEWHEEL:state.zoom(1.5 if flags>0 else 1/1.5,point)
        interaction['dirty']=True

    cv2.setMouseCallback(title,mouse)
    try:
        while True:
            if interaction['dirty']:
                canvas=np.zeros((815,1280,3),dtype=np.uint8)
                x1,y1,x2,y2=state.view;scale,left,top=state.layout()
                crop=cv2.cvtColor(frame[y1:y2,x1:x2],cv2.COLOR_RGB2BGR)
                image=cv2.resize(crop,(max(1,round((x2-x1)*scale)),max(1,round((y2-y1)*scale))))
                left,top=round(left),round(top)
                canvas[top:top+image.shape[0],left:left+image.shape[1]]=image
                for i,box in enumerate(state.boxes):
                    a,b=state.screen(*box[:2]),state.screen(*box[2:])
                    color=(50,230,50) if tuple(box) in state.confirmed else (0,180,255)
                    cv2.rectangle(canvas,a,b,color,2)
                    cv2.putText(canvas,str(i+1),a,cv2.FONT_HERSHEY_SIMPLEX,.5,color,1)
                if interaction['start']:
                    cv2.rectangle(canvas,state.screen(*interaction['start']),state.screen(*interaction['end']),(255,220,0),1)
                for left,right,text,_ in buttons:
                    cv2.rectangle(canvas,(left,5),(right,45),(65,65,65),-1)
                    cv2.putText(canvas,text,(left+6,30),cv2.FONT_HERSHEY_SIMPLEX,.55,(255,255,255),1)
                cv2.rectangle(canvas,(0,755),(1280,815),(0,0,0),-1)
                cv2.putText(canvas,message[:155],(8,778),cv2.FONT_HERSHEY_SIMPLEX,.5,(255,255,255),1)
                cv2.putText(canvas,f'{len(state.selected())} GREEN selected / {len(state.boxes)} shown | orange ignored | no portraits, allies or groups',
                            (8,803),cv2.FONT_HERSHEY_SIMPLEX,.5,(0,180,255),1)
                cv2.imshow(title,canvas);interaction['dirty']=False
            key=cv2.waitKey(30)
            try:
                if cv2.getWindowProperty(title,cv2.WND_PROP_VISIBLE)<1:return None
            except cv2.error:return None
            action=interaction['action']
            if action is None and key>=0:action=key_action(key&255)
            interaction['action']=None
            if action=='save':
                selected=state.selected()
                issues=annotation_issues(selected)
                if selected and not issues:return selected
                message=issues[0] if issues else 'No GREEN boxes. Click real enemies, or Cancel then N (enemy-free) / X (replay/menu).'
            elif action=='cancel':return None
            elif action=='undo' and state.boxes:state.remove(state.boxes[-1])
            elif action=='clear':state.boxes.clear();state.confirmed.clear()
            elif action=='plus':state.zoom(1.5)
            elif action=='minus':state.zoom(1/1.5)
            elif action=='reset':state.view=(0,0,state.width,state.height)
            elif action=='suggest' and suggest is not None:
                try:
                    proposed=suggest(state.view)
                    for box in proposed:state.add(box,confirmed=False)
                    message=f'Added {len(proposed)} orange drafts. Click only real enemies GREEN; portraits/allies stay unselected.'
                except (OSError,RuntimeError,ValueError) as error:message=str(error)
            if action:interaction['dirty']=True
    finally:
        try:cv2.destroyWindow(title)
        except cv2.error:pass
