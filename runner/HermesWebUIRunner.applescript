property lifecycleScript : "/Users/frenzy/dev/hermes/hermes-tools/runner/hermes-webui-app.sh"
property webUIURL : "http://127.0.0.1:8787"

on run
  try
    do shell script quoted form of lifecycleScript & " start"
    open location webUIURL
  on error errorMessage number errorNumber
    display dialog "Hermes WebUI could not start:" & return & errorMessage buttons {"OK"} default button "OK" with icon stop
    quit
  end try
end run

on reopen
  open location webUIURL
end reopen

on idle
  return 86400
end idle

on quit
  try
    do shell script quoted form of lifecycleScript & " stop"
  on error errorMessage number errorNumber
    display dialog "Hermes WebUI could not stop:" & return & errorMessage buttons {"OK"} default button "OK" with icon caution
  end try
  continue quit
end quit
