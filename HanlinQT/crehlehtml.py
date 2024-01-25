import os, sys, argparse

def create(questions, savename):
    template = open('hleqa.html', 'r', encoding="utf-8").read()
    replaced = template.replace("['QAJSON']", str(questions))
    open(savename, 'w', encoding="utf-8").write(replaced)

if __name__=='__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('QuestionFile', type=str, help='Question File Location')
    parser.add_argument('SaveName', type=str, help='HTML save name')
    arg = parser.parse_args()
    q = open(arg.QuestionFile, 'r', encoding="utf-8").read()
    if arg.SaveName.lower().endswith('.html'):
        savename = arg.SaveName
    else:
        savename = arg.SaveName + '.html'
    print(f'Saving Questions to {savename}')
    create(q, savename)
    print("Done")
