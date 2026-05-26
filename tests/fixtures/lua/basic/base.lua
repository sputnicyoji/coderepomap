local M = {}

function M.shared()
  return "shared"
end

function M:helper(value)
  return value * 2
end

return M
