local GameObject = CS.UnityEngine.GameObject
local Debug = CS.UnityEngine.Debug

local M = {}

function M.start()
  local go = GameObject.Find("Main")
  Debug.Log("started")
end

return M
