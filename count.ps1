$s = Get-Content app.py -Raw
$matches = ([regex]'log_unexpected').Matches($s)
Write-Output $matches.Count
