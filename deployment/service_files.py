"""Generate reviewable startup definitions; never install or enable them."""
import argparse
import json
import plistlib
from pathlib import Path, PurePosixPath, PureWindowsPath
import xml.etree.ElementTree as ET


def definitions(platform,project,user,*,instance_config,python=None):
    if any(c in project+user+instance_config+(python or '') for c in '\r\n\x00'):raise ValueError('Paths/accounts must be single-line.')
    if not user:raise ValueError('Specify the operating-system account that owns the project.')
    windows=platform=='windows'
    root=PureWindowsPath(project) if windows else PurePosixPath(project)
    if not root.is_absolute():raise ValueError('Project path must be absolute.')
    interpreter=root/'.venv'/('Scripts/python.exe' if windows else 'bin/python')
    path_type=PureWindowsPath if windows else PurePosixPath
    config=path_type(instance_config)
    if not config.is_absolute():raise ValueError('Absolute instance config required.')
    if python is not None:
        interpreter=path_type(python)
        if not interpreter.is_absolute():raise ValueError('Absolute interpreter required.')
    result={}
    for component in ('dashboard','worker','scheduler'):
        args=['-u','-m','deployment.launch','--component',component,'--config',str(config)]
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
            for tag,value in [('MultipleInstancesPolicy','IgnoreNew'),('DisallowStartIfOnBatteries','false'),('StopIfGoingOnBatteries','false'),('StartWhenAvailable','true'),('Enabled','true'),('ExecutionTimeLimit','PT0S')]:child(settings,tag,value)
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
            if any(c in project+str(config)+str(interpreter) for c in '"%\\$'):raise ValueError('Unsupported systemd path character.')
            command=' '.join('"'+a+'"' for a in [str(interpreter),*args])
            result[name+'.service']=(f'[Unit]\nDescription=Chief Agent {component}\nAfter=network.target\nStartLimitIntervalSec=120\nStartLimitBurst=3\n\n[Service]\nType=simple\nWorkingDirectory={root}\nExecStart={command}\nExecStop={command} --stop\nTimeoutStopSec=30\nKillMode=control-group\nNoNewPrivileges=yes\nRestart=on-failure\nRestartSec=10\nUMask=0077\n\n[Install]\nWantedBy=default.target\n').encode()
        else:raise ValueError('Supported targets: linux, windows, macos.')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--platform',choices=['linux','windows','macos'],required=True)
    parser.add_argument('--project',required=True);parser.add_argument('--user',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--instance-config',required=True);parser.add_argument('--python')
    args=parser.parse_args();files=definitions(args.platform,args.project,args.user,instance_config=args.instance_config,python=args.python)
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    for name,data in files.items():
        with (out/name).open('xb') as stream:stream.write(data)
    print('Generated definitions only. Review Deployment-platforms.txt before installing. No services changed.')

if __name__=='__main__':main()
