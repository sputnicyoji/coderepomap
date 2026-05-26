local GameObject = CS.UnityEngine.GameObject
local Debug = CS.UnityEngine.Debug
local Base = require "base"

local Controller = {}

function Controller.start()
  local go = GameObject.Find("Main")
  Debug.Log("started")
  Base.shared()
end

function Controller:onTick()
  Debug.Log("tick")
end

return Controller
