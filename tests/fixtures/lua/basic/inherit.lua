local Base = require "base"

local Child = setmetatable({}, {__index = Base})

function Child:new()
  local self = setmetatable({}, {__index = Child})
  return self
end

function Child.create()
  return Child:new()
end

return Child
