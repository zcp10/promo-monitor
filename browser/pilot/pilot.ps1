param([ValidateSet('Start','Status','Stop','Screenshot')][string]$Action='Status')
$ErrorActionPreference='Stop'
$pilotRoot=$PSScriptRoot
$baseImage = if ($env:PILOT_BASE_IMAGE) { $env:PILOT_BASE_IMAGE } else { 'python:3.11-slim' }
$pilotImage = if ($env:PILOT_IMAGE_TAG) { $env:PILOT_IMAGE_TAG } else { 'promo-monitor-pilot:latest' }
$dockerArgs=@('--config',(Join-Path $pilotRoot 'docker-config'),'--host','npipe:////./pipe/dockerDesktopLinuxEngine')
function DockerRun([string[]]$Arguments) {
    $result=& docker @dockerArgs @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker falhou: $($Arguments[0])" }
    return $result
}
if ($Action -eq 'Stop') {
    DockerRun @('stop','radar-google-pilot','radar-google-egress')
    exit
}
if ($Action -eq 'Screenshot') {
    $encoded=DockerRun @('exec','radar-google-pilot','python','-c',"import base64;print(base64.b64encode(open('/state/latest.png','rb').read()).decode())")
    $destination=Join-Path $pilotRoot 'latest.png'
    [IO.File]::WriteAllBytes($destination,[Convert]::FromBase64String(($encoded -join '')))
    Write-Output $destination
    exit
}
if ($Action -eq 'Start') {
    if ($env:PILOT_BASE_DIGEST) {
        $baseId=DockerRun @('image','inspect',$baseImage,'--format','{{.Id}}')
        if ($baseId -ne $env:PILOT_BASE_DIGEST) { throw 'A imagem base configurada mudou; revisar antes de construir.' }
    }
    $images=DockerRun @('image','ls','--format','{{.Repository}}:{{.Tag}}')
    if ($images -notcontains $pilotImage) {
        DockerRun @('build','--build-arg',"BASE_IMAGE=$baseImage",'-t',$pilotImage,$pilotRoot)
    }
    $networks=DockerRun @('network','ls','--format','{{.Name}}')
    if ($networks -notcontains 'radar-google-private') { DockerRun @('network','create','--internal','radar-google-private') }
    $volumes=DockerRun @('volume','ls','--format','{{.Name}}')
    if ($volumes -notcontains 'radar-google-profile') { DockerRun @('volume','create','radar-google-profile') }
    $containers=DockerRun @('ps','-a','--format','{{.Names}}')
    if ($containers -notcontains 'radar-google-egress') {
        DockerRun @('run','-d','--name','radar-google-egress','--restart','unless-stopped','--read-only','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--tmpfs','/tmp:rw,nosuid,nodev,size=32m,mode=1777',
            '--memory','128m','--cpus','0.5','--pids-limit','64',
            '--log-opt','max-size=5m','--log-opt','max-file=2',
            $pilotImage,'python','/pilot/proxy.py')
        DockerRun @('network','connect','radar-google-private','radar-google-egress')
    } else { DockerRun @('start','radar-google-egress') }
    $proxyInfo=(((DockerRun @('inspect','radar-google-egress')) -join "`n") | ConvertFrom-Json)
    $proxyIP=$proxyInfo[0].NetworkSettings.Networks.'radar-google-private'.IPAddress
    if (-not $proxyIP) { throw 'Proxy sem endereço interno.' }
    # Docker may assign a different private IP after a host restart.
    if ($containers -contains 'radar-google-pilot') {
        $workerInfo=(((DockerRun @('inspect','radar-google-pilot')) -join "`n") | ConvertFrom-Json)
        $expectedProxy="GOOGLE_PROXY=http://${proxyIP}:8080"
        if ($workerInfo[0].Config.Env -notcontains $expectedProxy) {
            DockerRun @('stop','radar-google-pilot')
            DockerRun @('rm','radar-google-pilot')
            $containers=@($containers | Where-Object { $_ -ne 'radar-google-pilot' })
        }
    }
    if ($containers -notcontains 'radar-google-pilot') {
        DockerRun @('run','-d','--name','radar-google-pilot','--restart','unless-stopped',
            '--network','radar-google-private','--dns','127.0.0.1','--read-only',
            '--cap-drop','ALL','--cap-add','SYS_CHROOT','--security-opt','no-new-privileges',
            '--security-opt',('seccomp='+(Join-Path $pilotRoot 'seccomp_profile.json')),
            '--tmpfs','/tmp:rw,nosuid,nodev,size=1536m,mode=1777','--shm-size','512m',
            '--memory','3g','--cpus','2','--pids-limit','384',
            '--log-opt','max-size=5m','--log-opt','max-file=2',
            '--mount','type=volume,src=radar-google-profile,dst=/state',
            '-e',("GOOGLE_PROXY=http://${proxyIP}:8080"),$pilotImage)
    } else { DockerRun @('start','radar-google-pilot') }
    Start-Sleep -Seconds 3
}
'{"action":"health"}' | & docker @dockerArgs exec -i radar-google-pilot python /pilot/client.py
if ($LASTEXITCODE -ne 0) { throw 'Coletor indisponível. Verifique se o Docker Desktop está aberto.' }
