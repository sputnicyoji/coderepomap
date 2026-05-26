using System;
using System.Collections.Generic;

namespace Game.Core
{
    public class GameManager : IManager
    {
        public string Name { get; set; }
        public int Score { get; private set; }

        public GameManager()
        {
            Name = "";
            Score = 0;
        }

        public void Init()
        {
            Score = 0;
        }

        public void AddScore(int delta)
        {
            Score += delta;
        }

        public void AddScore(string source, int delta)
        {
            AddScore(delta);
        }

        public void AddScore(int delta, bool combo)
        {
            if (combo) Score += delta * 2;
            else Score += delta;
        }
    }

    public interface IManager
    {
        void Init();
    }
}
