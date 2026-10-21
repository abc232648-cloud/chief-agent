"""Generate reviewable startup definitions; never install or enable them."""
import argparse
import json
import plistlib
import re
from pathlib import Path, PurePosixPath, PureWindowsPath
import xml.etree.ElementTree as ET


def _linux_account(value):
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}', value or ''):
        raise ValueError('Linux service owner must be one explicit local account name.')
    return value


def definitions(platform,project,user,*,instance_config,python=None,managed_installation=None):
    if any(c in project+user+instance_config+(python or '')+(managed_installation or '') for c in '\r\n\x00'):raise ValueError('Paths/accounts must be single-line.')
    if not user:raise ValueError('Specify the operating-system account that owns the project.')
    windows=platform=='windows'
    def _to_path(p):
        if not windows and isinstance(p, str) and re.match(r'^[a-zA-Z]:', p):
            p = re.sub(r'^[a-zA-Z]:/*', '/', p.replace('\\', '/'))
        return PureWindowsPath(p) if windows else PurePosixPath(p)
    root=_to_path(project)
    if not root.is_absolute():raise ValueError('Project path must be absolute.')
    interpreter=root/'.venv'/('Scripts/pythonw.exe' if windows else 'bin/python')
    config=_to_path(instance_config)
    if not config.is_absolute():raise ValueError('Absolute instance config required.')
    if managed_installation is not None:
        managed=_to_path(managed_installation)
        if not managed.is_absolute():raise ValueError('Absolute managed installation required.')
        if root==managed or root.is_relative_to(managed):raise ValueError('Stable launcher source must stay outside managed releases.')
    if python is not None:
        interpreter=_to_path(python); interpreter = interpreter.with_name("pythonw.exe") if (windows and interpreter.name.lower() == "python.exe") else interpreter
        if not interpreter.is_absolute():raise ValueError('Absolute interpreter required.')
    if platform=='linux':_linux_account(user)
    result={}
    for component in ('dashboard','worker','scheduler'):
        args=['-u','-m','deployment.launch','--component',component,'--config',str(config)]
        if managed_installation is not None:
            args=['-B','-E','-s','-u','-m','deployment.managed_launch','--component',component,'--installation',str(managed)]
        name='chief-agent-'+component
        if platform=='windows':
            from subprocess import list2cmdline
            task=ET.Element('Task',{'version':'1.4','xmlns':'http://schemas.microsoft.com/windows/2004/02/mit/task'})
            def child(parent,tag,value=None):
                node=ET.SubElement(parent,tag)
                if value is not None:node.text=value
                return node
            info=child(task,'RegistrationInfo');child(info,'Description','Chief Agent '+component)
            triggers=child(task,'Triggers');boot=child(triggers,'LogonTrigger');child(boot,'Enabled','true');child(boot,'UserId',user);child(boot,'Delay','PT30S')
            principal=child(child(task,'Principals'),'Principal');principal.set('id','Owner')
            child(principal,'UserId',user);child(principal,'LogonType','InteractiveToken');child(principal,'RunLevel','LeastPrivilege')
            settings=child(task,'Settings')
            for tag,value in [('MultipleInstancesPolicy','IgnoreNew'),('DisallowStartIfOnBatteries','false'),('StopIfGoingOnBatteries','false'),('StartWhenAvailable','true'),('Enabled','true'),('ExecutionTimeLimit','PT0S'),('Hidden','true')]:child(settings,tag,value)
            restart=child(settings,'RestartOnFailure');child(restart,'Interval','PT1M');child(restart,'Count','3')
            actions=child(task,'Actions');actions.set('Context','Owner');execute=child(actions,'Exec')
            child(execute,'Command',str(interpreter));child(execute,'Arguments',list2cmdline(args));child(execute,'WorkingDirectory',str(root))
            # Task Scheduler consumes a UTF-16 BSTR when imported with -Xml.
            result[name+'.xml']=ET.tostring(task,encoding='utf-16',xml_declaration=True)
        elif platform=='macos':
            if not root.is_relative_to('/Users') and not root.is_relative_to('/opt'):raise ValueError('Use an absolute /Users or /opt project path on macOS.')
            value={'Label':'local.'+name,'ProgramArguments':[str(interpreter),*args],'WorkingDirectory':str(root),'UserName':user,'RunAtLoad':True,'KeepAlive':{'SuccessfulExit':False},'ThrottleInterval':10,'Umask':0o077,'StandardOutPath':str(root/'logs'/(component+'.out.log')),'StandardErrorPath':str(root/'logs'/(component+'.err.log'))}
            result['local.'+name+'.plist']=plistlib.dumps(value)
        elif platform=='linux':
            managed_str = str(managed) if managed_installation is not None else ''
            if any(c in str(root)+str(config)+str(interpreter)+managed_str for c in '"%\\$'):raise ValueError('Unsupported systemd path character.')
            command=' '.join('"'+a+'"' for a in [str(interpreter),*args])
            result[name+'.service']=(f'[Unit]\nDescription=Chief Agent {component}\nConditionUser={user}\nAfter=network.target\nStartLimitIntervalSec=120\nStartLimitBurst=3\n\n[Service]\nType=simple\nWorkingDirectory={root}\nExecStart={command}\nExecStop={command} --stop\nTimeoutStopSec=30\nKillMode=control-group\nNoNewPrivileges=yes\nRestart=on-failure\nRestartSec=10\nUMask=0077\n\n[Install]\nWantedBy=default.target\n').encode()
        else:raise ValueError('Supported targets: linux, windows, macos.')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform',choices=['linux','windows','macos'],required=True)
    parser.add_argument('--project',required=True);parser.add_argument('--user',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--instance-config',required=True);parser.add_argument('--python')
    parser.add_argument('--managed-installation',help='Optional private source-slot root; project must be a stable external launcher copy.')
    args=parser.parse_args();files=definitions(args.platform,args.project,args.user,instance_config=args.instance_config,python=args.python,managed_installation=args.managed_installation)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    for name,data in files.items():
        with (out/name).open('xb') as stream:stream.write(data)
    print('Generated definitions only. Review Deployment-platforms.txt before installing. No services changed.')

if __name__=='__main__':main()
