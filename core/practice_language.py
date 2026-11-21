"""Capability-limited scalar programming language boundary for C++ and Python.

Parse model source to a small IR and compile ONLY trusted code emitted from that
IR. No source eval, imports, pointers, attributes, arbitrary calls, file/network
operations, arrays or compiler directives. This is a language boundary, not an
OS sandbox for arbitrary programs. All operations and loops have explicit bounds.
"""
import ast
import re

LIMIT = 1_000_000_000
STEPS = 10_000
OPS = {ast.Add:'add', ast.Sub:'sub', ast.Mult:'mul', ast.FloorDiv:'floor', ast.Mod:'mod'}
CMP = {ast.Lt:'<', ast.LtE:'<=', ast.Gt:'>', ast.GtE:'>=', ast.Eq:'==', ast.NotEq:'!='}
NAME = re.compile(r'[A-Za-z][A-Za-z0-9_]{0,30}\Z')


def name(value):
    if not NAME.fullmatch(value) or value in {'solve', 'abs', 'min', 'max', 'range'}:
        raise ValueError('Only simple local variable names are allowed')
    return value


def checked(value):
    if not isinstance(value, int) or abs(value) > LIMIT:
        raise ValueError('Integer magnitude limit exceeded')
    return int(value)


def from_python(source):
    if not isinstance(source, str) or len(source) > 10000:
        raise ValueError('Source limit exceeded')
    tree = ast.parse(source)
    if sum(1 for _ in ast.walk(tree)) > 500 or len(tree.body) != 1:
        raise ValueError('Submit exactly one bounded solve(a,b) function')
    f = tree.body[0]
    if (not isinstance(f, ast.FunctionDef) or f.name != 'solve' or f.decorator_list
        or [a.arg for a in f.args.args] != ['a','b'] or f.args.defaults
        or f.args.kwonlyargs or f.args.posonlyargs or f.args.vararg or f.args.kwarg
        or getattr(f, 'type_params', [])):
        raise ValueError('Required signature: def solve(a,b), with no decorators or defaults')
    annotations = [f.returns] + [a.annotation for a in f.args.args]
    if any(a is not None and not (isinstance(a, ast.Name) and a.id == 'int') for a in annotations):
        raise ValueError('Only optional int annotations are allowed')

    def expr(n):
        if isinstance(n, ast.Constant) and type(n.value) is int:
            return ['num', checked(n.value)]
        if isinstance(n, ast.Name): return ['var', name(n.id)]
        if isinstance(n, ast.BinOp) and type(n.op) in OPS:
            return ['bin', OPS[type(n.op)], expr(n.left), expr(n.right)]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd, ast.Not)):
            return ['un', {ast.USub:'-',ast.UAdd:'+',ast.Not:'!'}[type(n.op)], expr(n.operand)]
        if isinstance(n, ast.Compare) and len(n.ops)==1 and type(n.ops[0]) in CMP:
            return ['bin', CMP[type(n.ops[0])], expr(n.left), expr(n.comparators[0])]
        if isinstance(n, ast.BoolOp):
            result = expr(n.values[0])
            for v in n.values[1:]: result = ['bin', '&&' if isinstance(n.op,ast.And) else '||', result, expr(v)]
            return result
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and not n.keywords:
            if n.func.id == 'abs' and len(n.args)==1: return ['call','abs',expr(n.args[0])]
            if n.func.id in {'min','max'} and len(n.args)==2: return ['call',n.func.id,*map(expr,n.args)]
        raise ValueError('Unsupported expression; no attributes, imports, strings or arbitrary calls')

    def statements(nodes):
        out=[]
        for n in nodes:
            if isinstance(n, ast.Assign) and len(n.targets)==1:
                target = n.targets[0]
                if isinstance(target, ast.Name): out.append(['set',[name(target.id)],[expr(n.value)]])
                elif (isinstance(target, ast.Tuple) and isinstance(n.value, ast.Tuple)
                      and len(target.elts)==len(n.value.elts)<=4 and all(isinstance(x,ast.Name) for x in target.elts)):
                    out.append(['set',[name(x.id) for x in target.elts],list(map(expr,n.value.elts))])
                else: raise ValueError('Only local scalar assignments are allowed')
            elif isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) and type(n.op) in OPS:
                var=name(n.target.id); out.append(['set',[var],[['bin',OPS[type(n.op)],['var',var],expr(n.value)]]])
            elif isinstance(n, ast.Return) and n.value is not None: out.append(['return',expr(n.value)])
            elif isinstance(n, ast.If): out.append(['if',expr(n.test),statements(n.body),statements(n.orelse)])
            elif isinstance(n, ast.While) and not n.orelse: out.append(['while',expr(n.test),statements(n.body)])
            elif (isinstance(n, ast.For) and isinstance(n.target,ast.Name) and not n.orelse
                  and isinstance(n.iter,ast.Call) and isinstance(n.iter.func,ast.Name)
                  and n.iter.func.id=='range' and not n.iter.keywords and len(n.iter.args) in (1,2)):
                start,end=(['num',0],expr(n.iter.args[0])) if len(n.iter.args)==1 else tuple(map(expr,n.iter.args))
                out.append(['for',name(n.target.id),start,end,statements(n.body)])
            elif isinstance(n, ast.Pass): out.append(['pass'])
            else: raise ValueError('Unsupported statement; use assignments, if, while, range and return')
        return out
    result=statements(f.body)
    validate_ir(result)
    return result


TOKEN = re.compile(r'\s+|//[^\n]*|[A-Za-z][A-Za-z0-9_]*|[0-9]+|<=|>=|==|!=|&&|\|\||\+\+|--|\+=|-=|\*=|[%+*/<>=!;,(){}-]')


class CppParser:
    def __init__(self, source):
        if not isinstance(source,str) or len(source)>10000: raise ValueError('Source limit exceeded')
        self.tokens=[]; pos=0
        while pos<len(source):
            m=TOKEN.match(source,pos)
            if not m: raise ValueError('C++ source contains unsupported token or directive')
            t=m.group(); pos=m.end()
            if not t.isspace() and not t.startswith('//'): self.tokens.append(t)
        if len(self.tokens)>500: raise ValueError('Too many source tokens')
        self.i=0; self.variables={'a','b'}

    def peek(self): return self.tokens[self.i] if self.i<len(self.tokens) else ''
    def take(self, wanted=None):
        t=self.peek()
        if not t or (wanted is not None and t!=wanted): raise ValueError('Expected '+str(wanted)+', found '+repr(t))
        self.i+=1; return t
    def typ(self):
        t=self.take()
        if t=='long': self.take('long')
        elif t!='int': raise ValueError('Only int and long long types are supported')
    def expression(self, precedence=0):
        t=self.take()
        if t in ('-','+','!'): left=['un',t,self.expression(8)]
        elif t=='(': left=self.expression(); self.take(')')
        elif t.isdigit(): left=['num',checked(int(t))]
        elif t in {'abs','min','max'} and self.peek()=='(':
            self.take('('); args=[self.expression()]
            if t in {'min','max'}: self.take(','); args.append(self.expression())
            self.take(')'); left=['call',t,*args]
        else: left=['var',name(t)]
        levels={'||':1,'&&':2,'==':3,'!=':3,'<':4,'<=':4,'>':4,'>=':4,'+':5,'-':5,'*':6,'/':6,'%':6}
        mapping={'+':'add','-':'sub','*':'mul','/':'cdiv','%':'cmod'}
        while levels.get(self.peek(),0)>precedence:
            op=self.take(); right=self.expression(levels[op]); left=['bin',mapping.get(op,op),left,right]
        return left
    def block(self):
        self.take('{'); out=[]
        while self.peek()!='}': out.extend(self.statement())
        self.take('}'); return out
    def assignment(self, end=';'):
        declaration=self.peek() in {'long','int'}
        if declaration: self.typ()
        if self.peek() in {'++','--'}:
            op=self.take(); var=name(self.take()); result=['bin','add' if op=='++' else 'sub',['var',var],['num',1]]
        else:
            var=name(self.take()); op=self.take()
            if declaration:
                if var in self.variables: raise ValueError('Variable redeclaration is not supported')
                self.variables.add(var)
            elif var not in self.variables: raise ValueError('C++ locals must be declared before assignment')
            if op in {'++','--'}: result=['bin','add' if op=='++' else 'sub',['var',var],['num',1]]
            elif op in {'=','+=','-=','*='}:
                result=self.expression()
                if op!='=': result=['bin',{'+=':'add','-=':'sub','*=':'mul'}[op],['var',var],result]
            else: raise ValueError('Expected initialized local scalar assignment')
        self.take(end); return ['set',[var],[result]]
    def statement(self):
        t=self.peek()
        if t=='return': self.take(); e=self.expression(); self.take(';'); return [['return',e]]
        if t in {'if','while'}:
            self.take(); self.take('('); condition=self.expression(); self.take(')')
            body=self.block() if self.peek()=='{' else self.statement()
            if t=='while': return [['while',condition,body]]
            other=[]
            if self.peek()=='else':
                self.take(); other=self.block() if self.peek()=='{' else self.statement()
            return [['if',condition,body,other]]
        if t=='for':
            self.take(); self.take('('); init=self.assignment(); condition=self.expression(); self.take(';')
            update=self.assignment(')'); body=self.block() if self.peek()=='{' else self.statement()
            return [init,['while',condition,body+[update]]]
        if t==';': self.take(); return [['pass']]
        return [self.assignment()]
    def parse(self):
        self.typ(); self.take('solve'); self.take('('); self.typ(); self.take('a'); self.take(','); self.typ(); self.take('b'); self.take(')')
        body=self.block()
        if self.peek(): raise ValueError('Only one solve(a,b) function is allowed')
        validate_ir(body); return body


def validate_ir(body):
    """Enforce bounded recursion, variable identity and structural limits."""
    def walk(v, depth=0):
        if depth>30: raise ValueError('Nesting limit exceeded')
        if isinstance(v,list):
            for child in v: walk(child,depth+1)
    walk(body)
    def expression(e, known):
        if e[0]=='var' and e[1] not in known: raise ValueError('Variable read before initialization: '+e[1])
        for child in e[1:]:
            if isinstance(child,list): expression(child,known)
    def flow(nodes, known):
        known=set(known)
        for n in nodes:
            if n[0]=='set':
                for e in n[2]: expression(e,known)
                known.update(n[1])
            elif n[0]=='return': expression(n[1],known)
            elif n[0] in {'if','while'}:
                expression(n[1],known)
                after=flow(n[2],known)
                if n[0]=='if': known.update(after & flow(n[3],known))
            elif n[0]=='for':
                expression(n[2],known); expression(n[3],known)
                known.add(n[1]); flow(n[4],known)
        return known
    flow(body,{'a','b'})


def parse(source, language):
    if language=='python': return from_python(source)
    if language=='cpp': return CppParser(source).parse()
    raise ValueError('Supported languages: python, cpp')


def emit(body, language):
    """Generate canonical trusted source. Never interpolate unparsed model source."""
    cpp=language=='cpp'; variables={'a','b'}; lines=[]; serial=0
    def expr(e):
        k=e[0]
        if k=='num': return str(e[1])
        if k=='var': return 'v_'+e[1]
        if k=='un': return ('(!'+expr(e[2])+')' if cpp else '(not '+expr(e[2])+')') if e[1]=='!' else 'safe_'+('neg' if e[1]=='-' else 'pos')+'('+expr(e[2])+')'
        if k=='call': return 'safe_'+e[1]+'('+','.join(expr(x) for x in e[2:])+')'
        op=e[1]
        if op in {'add','sub','mul','floor','mod','cdiv','cmod'}: return 'safe_'+op+'('+expr(e[2])+','+expr(e[3])+')'
        op=op if cpp else {'&&':'and','||':'or'}.get(op,op)
        return '('+expr(e[2])+' '+op+' '+expr(e[3])+')'
    def block(nodes, indent):
        nonlocal serial
        for n in nodes:
            pad='    '*indent; lines.append(pad+('tick();' if cpp else 'tick()'))
            k=n[0]
            if k=='set':
                temps=[]
                for e in n[2]:
                    serial+=1; temp='temp'+str(serial); temps.append(temp)
                    lines.append(pad+('long long ' if cpp else '')+temp+' = '+expr(e)+(';' if cpp else ''))
                for var,temp in zip(n[1],temps):
                    variables.add(var); lines.append(pad+'v_'+var+' = bound('+temp+')'+(';' if cpp else ''))
            elif k=='return': lines.append(pad+'return bound('+expr(n[1])+')'+(';' if cpp else ''))
            elif k in {'if','while'}:
                lines.append(pad+k+(' ('+expr(n[1])+') {' if cpp else ' '+expr(n[1])+':'))
                if k=='while': lines.append(pad+'    '+('tick();' if cpp else 'tick()'))
                block(n[2],indent+1)
                if cpp: lines.append(pad+'}')
                if k=='if' and n[3]:
                    lines.append(pad+('else {' if cpp else 'else:')); block(n[3],indent+1)
                    if cpp: lines.append(pad+'}')
            elif k=='for':
                variables.add(n[1]); serial+=1; end='end'+str(serial)
                lines.append(pad+('long long ' if cpp else '')+end+' = '+expr(n[3])+(';' if cpp else ''))
                lines.append(pad+'v_'+n[1]+' = '+expr(n[2])+(';' if cpp else ''))
                loop=['while',['bin','<',['var',n[1]],['var','internal_end'+str(serial)]],n[4]+[['set',[n[1]],[['bin','add',['var',n[1]],['num',1]]]]]]
                # Emit the bound name explicitly, never allow user-named internal helpers.
                lines.append(pad+('while (v_'+n[1]+' < '+end+') {' if cpp else 'while v_'+n[1]+' < '+end+':'))
                block(loop[2],indent+1)
                if cpp: lines.append(pad+'}')
            else: lines.append(pad+(';' if cpp else 'pass'))
    block(body,1)
    if cpp:
        declarations='\n'.join('    long long v_'+v+'=0;' for v in sorted(variables-{'a','b'}))
        return 'long long solve(long long v_a,long long v_b) {\n'+declarations+'\n'+'\n'.join(lines)+'\n    throw std::runtime_error("missing return");\n}\n'
    return 'def solve(v_a,v_b):\n'+'\n'.join(lines)+'\n    raise RuntimeError("missing return")\n'
