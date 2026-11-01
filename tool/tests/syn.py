import ast,sys
for p in sys.argv[1:]:
    ast.parse(open(p, encoding='utf-8').read(), p)
    print('ok', p)
