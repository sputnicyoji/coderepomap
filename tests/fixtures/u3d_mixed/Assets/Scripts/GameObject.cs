using System;

namespace UnityEngine
{
    public class GameObject
    {
        public string Name { get; set; }

        public static GameObject Find(string name)
        {
            return new GameObject { Name = name };
        }
    }

    public static class Debug
    {
        public static void Log(string msg)
        {
        }
    }
}
