property stackScript : "/Users/frenzy/dev/hermes/hermes-tools/runner/stack.sh"
property webUIURL : "http://localhost:11001"

on run
  try
    do shell script quoted form of stackScript & " start"
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
    do shell script quoted form of stackScript & " stop"
  on error errorMessage number errorNumber
    display dialog "Hermes WebUI stack could not be stopped:" & return & errorMessage buttons {"OK"} default button "OK" with icon caution
  end try
  continue quit
end quit
